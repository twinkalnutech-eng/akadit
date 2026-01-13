from fastapi import FastAPI, HTTPException, BackgroundTasks
from core.database import get_connection
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional
from services.mail_service import send_ticket_email, send_email
from services.qr_pdf import create_ticket_pdf
from services.whatsapp_service import send_whatsapp_with_pdf
from api.validation_login import validate_user_credentials_in_db, validate_user_and_get_tickets
from utils.utils import decrypt_qr_data, generate_qr_string
from pydantic import BaseModel, EmailStr
import razorpay

load_dotenv()

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")
IMAGE_BASE_PATH = os.path.join(os.getcwd(), "static", "ticket_images")
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL")  
BASE_DIR = os.getcwd()
IMAGE_BASE_PATH = os.path.join(BASE_DIR, "static", "ticket_images")
razorpay_client = razorpay.Client(
    auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET"))
)

app = FastAPI(
    title="AKADIT API",
    docs_url=None,
    redoc_url=None
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://akadeet.com",
        "https://www.akadeet.com",
        "http://localhost:3000",
        "http://localhost:8138"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def list_only_project_routes():
    routes = []

    for route in app.routes:
        if not hasattr(route, "endpoint"):
            continue

        module = getattr(route.endpoint, "__module__", "")

        # Exclude FastAPI 
        if module.startswith("fastapi") or module.startswith("starlette"):
            continue

        methods = sorted(
            m for m in route.methods
            if m not in ("HEAD", "OPTIONS")
        )

        routes.append({
            "path": route.path,
            "methods": methods
        })

    return {
        "app": "AKADIT API",
        "status": "running",
        "total_routes": len(routes),
        "routes": routes
    }

@app.get("/health")
def health():
    try:
        conn = get_connection()
        conn.close()
        return {"status": "UP", "db": "connected"}
    except Exception as e:
        return {"status": "DOWN", "error": str(e)}

@app.get("/getEventList")
def get_ticketmaster():
    conn = get_connection()
    cursor = conn.cursor()

    query = """
    SELECT
        TicketMasterId,
        EventDate,
        EventDay,
        Venue,
        Country,
        CountryCode,
        Currency,
        EntryDateTime,
        EntryUserMasterId,
        MaxLimit,
        EnquiryToEmailId,
        BCCEmailId,
        EventPostpone,
        EventClose,
        EventName,
        EventTime
    FROM TicketMaster
    WHERE EventDate >= CAST(GETDATE() AS DATE)
    ORDER BY EventDate ASC
    """

    cursor.execute(query)

    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()

    data = [dict(zip(columns, row)) for row in rows]

    conn.close()

    return {
        "total_records": len(data),
        "tickets": data
    }


@app.get("/getEventTicketRate/{ticket_master_id}")
def get_event_rates(ticket_master_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT
            tm.TicketMasterId,
            tm.EventName,
            tc.TicketClassificationId,
            tc.TicketType,
            tc.TicketRate,
            tc.MinimumTickets
        FROM TicketMaster tm
        INNER JOIN TicketClassification tc
            ON tm.TicketMasterId = tc.TicketMasterId
        WHERE tm.TicketMasterId = ?
    """

    cursor.execute(query, ticket_master_id)

    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        return {"message": "No data found for this event"}

    data = [dict(zip(columns, row)) for row in rows]

    conn.close()

    return {
        "TicketMasterId": ticket_master_id,
        "EventName": data[0]["EventName"],
        "TicketRates": [
            {
                "TicketClassificationId": d["TicketClassificationId"],
                "TicketType": d["TicketType"],
                "TicketRate": d["TicketRate"],
                "MinimumTickets": d["MinimumTickets"]
            }
            for d in data
        ]
    }

class TicketEnquiryRequest(BaseModel):
    ticket_master_id: int
    name: str
    mobile_no: str
    email_id: str
    ticket_count: int

# =========================
# SAVE ENQUIRY API
# =========================
@app.post("/addTicketEnquiry")
def save_ticket_enquiry(data: TicketEnquiryRequest):

    if data.ticket_count <= 0:
        raise HTTPException(status_code=400, detail="Invalid ticket count")

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # =========================
        # 1. GET TICKET RATE
        # =========================
        cursor.execute("""
            SELECT TOP 1 TicketRate, MinimumTickets
            FROM TicketClassification
            WHERE TicketMasterId = ?
        """, data.ticket_master_id)

        rate_row = cursor.fetchone()

        if not rate_row:
            raise HTTPException(status_code=404, detail="Ticket rate not found")

        ticket_rate = rate_row[0]
        minimum_tickets = rate_row[1]

        # =========================
        # 2. VALIDATE MINIMUM TICKETS
        # =========================
        if data.ticket_count < minimum_tickets:
            raise HTTPException(
                status_code=400,
                detail=f"Minimum {minimum_tickets} tickets required"
            )

        # =========================
        # 3. CALCULATE TOTAL
        # =========================
        total_amount = ticket_rate * data.ticket_count

        # =========================
        # 4. INSERT ENQUIRY
        # =========================
        cursor.execute("""
            INSERT INTO TicketEnquiry
            (
                TicketMasterId,
                MobileNo,
                EmailId,
                TicketCount,
                TotalAmount,
                EntryDateTime,
                Name,
                IsSend
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.ticket_master_id,
            data.mobile_no,
            data.email_id,
            data.ticket_count,
            total_amount,
            datetime.now(),
            data.name,
            0
        ))

        conn.commit()

        return {
            "status": "success",
            "ticket_rate": ticket_rate,
            "ticket_count": data.ticket_count,
            "total_amount": total_amount,
            "message": "Ticket enquiry saved successfully"
        }

    except HTTPException:
        raise

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        conn.close()

class TicketIssueRequest(BaseModel):
    ticket_master_id: int
    ticket_classification_id: int
    name: str
    mobile_no: str
    email_id: str
    ticket_count: int
    transaction_id: Optional[str] = None

def send_email_and_whatsapp(
    email_id,
    name,
    mobile_no,
    entry_datetime,
    ticket_count,
    total_amount,
    pdf_files
):
    # EMAIL
    send_ticket_email(
        email_id,
        name,
        mobile_no,
        entry_datetime,
        ticket_count,
        total_amount,
        "USD",
        "Event Name",
        None,
        pdf_files
    )

    # WHATSAPP (ONE MESSAGE PER TICKET)
    for i, pdf in enumerate(pdf_files, start=1):
        send_whatsapp_with_pdf(
            mobile_no=mobile_no,
            pdf_file=pdf,
            ticket_no=i,
            total_tickets=ticket_count
        )


class QRScanRequest(BaseModel):
    qrCode: str

@app.post("/qrScanner")
def scan_qr(data: QRScanRequest):
    conn = None
    cursor = None

    try:
        # ----------------------------
        # 1. Empty QR check
        # ----------------------------
        if not data.qrCode or not data.qrCode.strip():
            return {
                "status": 2,
                "message": "QR code cannot be empty"
            }

        # ----------------------------
        # 2. Decrypt QR
        # ----------------------------
        try:
            decoded = decrypt_qr_data(data.qrCode)
        except Exception:
            return {
                "status": 2,
                "message": "Invalid QR code"
            }

        # ----------------------------
        # 3. Validate QR format
        # ----------------------------
        try:
            ticket_issue_id, details_id, ts = decoded.split("|")
            details_id = int(details_id)
        except Exception:
            return {
                "status": 2,
                "message": "Invalid QR code format"
            }

        # ----------------------------
        # 4. DB check
        # ----------------------------
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT IsPersonEntered
            FROM TicketIssueDetails
            WHERE TicketIssueDetailsId = ?
        """, details_id)

        row = cursor.fetchone()

        if not row:
            return {
                "status": 2,
                "message": "Invalid ticket"
            }

        # ----------------------------
        # 5. Already used
        # ----------------------------
        if row.IsPersonEntered:
            return {
                "status": 1,
                "message": "Ticket already used"
            }

        # ----------------------------
        # 6. Mark entry
        # ----------------------------
        cursor.execute("""
            UPDATE TicketIssueDetails
            SET IsPersonEntered = 1,
                EntryDateTime = GETDATE()
            WHERE TicketIssueDetailsId = ?
        """, details_id)

        conn.commit()

        # ----------------------------
        # 7. Success
        # ----------------------------
        return {
            "status": 0,
            "message": "Entry allowed",
            "ticket_issue_id": int(ticket_issue_id),
            "ticket_issue_details_id": details_id
        }

    except Exception as e:
        return {
            "status": 2,
            "message": "Internal server error"
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

class LoginRequest(BaseModel):
    username: str
    password: str
    ticket_master_id: int


@app.post("/userLogin")
def validate_user_credentials(model: LoginRequest):
    try:
        is_valid = validate_user_credentials_in_db(
            model.username,
            model.password
        )

        if is_valid:
            return {
                "status": 1,
                "message": "Login successful"
            }

        return {
            "status": 0,
            "message": "Invalid username or password"
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )


@app.post("/getReportData")
def scanner_login(data: LoginRequest):

    result = validate_user_and_get_tickets(
        data.username,
        data.password,
        data.ticket_master_id
    )

    if not result.is_valid_user:
        return {
            "success": False,
            "message": "Invalid username or password"
        }

    return {
        "tickets": result.tickets,
        "summary": result.summary
    }


class BannerLoginRequest(BaseModel):
    ticket_master_id: int

@app.post("/banner_image")
def get_event_by_master_id(data: BannerLoginRequest):

    if data.ticket_master_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid TicketMasterId")

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                Image1,
                Image2,
                Image3,
                Image4,
                Image5,
                Image6
            FROM TicketMaster
            WHERE TicketMasterId = ?
        """, (data.ticket_master_id,))

        row = cursor.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Event not found")

        images = {}
        for i in range(1, 7):
            img = getattr(row, f"Image{i}", None)
            images[f"image{i}"] = f"{IMAGE_BASE_URL}/{data.ticket_master_id}/{img}" if img else None

        return {"images": images}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

class StallMasterRequest(BaseModel):
    stall_no: str
    event_master_id: int
    stall_expenses: float
    eminities: Optional[str] = None
    deposit_amount: float
    entry_user_master_id: int

@app.post("/addStallMaster")
def add_stall_master(data: StallMasterRequest):
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = """
        INSERT INTO [EventManagement].[dbo].[StallMaster]
        (
            StallNo,
            EventMasterId,
            StallExpenses,
            Eminities,
            DepositAmount,
            EntryDateTime,
            EntryUserMasterId
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """

        cursor.execute(
            query,
            (
                data.stall_no,
                data.event_master_id,
                data.stall_expenses,
                data.eminities,
                data.deposit_amount,
                datetime.now(),
                data.entry_user_master_id
            )
        )

        conn.commit()

        return {
            "status": 1,
            "message": "Stall created successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


class CategoryRequest(BaseModel):
    category_name: str
    category_type: str
    entry_user_master_id: int


@app.post("/addCategory")
def add_category(data: CategoryRequest):
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = """
        INSERT INTO [EventManagement].[dbo].[CategoryMaster]
        (
            CategoryName,
            CategoryType,
            EntryDateTime,
            EntryUserMasterId
        )
        VALUES (?, ?, ?, ?)
        """

        cursor.execute(
            query,
            (
                data.category_name,
                data.category_type,
                datetime.now(),
                data.entry_user_master_id
            )
        )

        conn.commit()

        return {
            "status": 1,
            "message": "Category added successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


class StallBookingMasterRequest(BaseModel):
    EventMasterId: int
    TenantName: str
    TenantBrandName: str | None = None
    TenantEmail: EmailStr | None = None
    TenantContactNo: str | None = None
    SocialMediaLink: str | None = None
    CategoryId: int
    IsExecutedBefore: bool = False
    SpecialRequirement: str | None = None
    EntryUserMasterId: int

# --------------------------
# API Endpoint
# --------------------------
@app.post("/addStallBookingMaster")
def add_stall_booking_master(data: StallBookingMasterRequest):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # ---------------------------
        # Insert Stall Booking
        # ---------------------------
        query = """
            INSERT INTO [dbo].[StallBookingMaster]
            (EventMasterId, TenantName, TenantBrandName, TenantEmail, TenantContactNo,
             SocialMediaLink, CategoryId, IsExecutedBefore, SpecialRequirement, EntryUserMasterId)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        cursor.execute(
            query,
            (
                data.EventMasterId,
                data.TenantName,
                data.TenantBrandName,
                data.TenantEmail,
                data.TenantContactNo,
                data.SocialMediaLink,
                data.CategoryId,
                int(data.IsExecutedBefore), 
                data.SpecialRequirement,
                data.EntryUserMasterId
            )
        )
        conn.commit()

        # ---------------------------
        # Send Confirmation Email
        # ---------------------------
        if data.TenantEmail:
            email_subject = "Stall Booking Confirmed"
            email_body = f"""
            Hello {data.TenantName},

            Your stall booking has been successfully confirmed for Event ID: {data.EventMasterId}.

            Booking Details:
            Tenant Name: {data.TenantName}
            Brand Name: {data.TenantBrandName}
            Contact No: {data.TenantContactNo}
            Category ID: {data.CategoryId}
            Special Requirements: {data.SpecialRequirement}

            Thank you for choosing our event.
            """
            send_email(data.TenantEmail, email_subject, email_body)

        return {
            "status": 1,
            "message": "Stall booking confirmed successfully and email sent"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.get("/getStallBookingMasters")
def get_stall_booking_masters():
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                sbm.[StallBookingMasterId],
                tm.[EventName] AS EventName,
                sbm.[TenantName],
                sbm.[TenantBrandName],
                sbm.[TenantEmail],
                sbm.[TenantContactNo],
                sbm.[SocialMediaLink],
                cm.[CategoryName] AS CategoryName,
                sbm.[IsExecutedBefore],
                sbm.[SpecialRequirement]
            FROM [EventManagement].[dbo].[StallBookingMaster] sbm
            LEFT JOIN [EventManagement].[dbo].[TicketMaster] tm
                ON sbm.EventMasterId = tm.TicketMasterId
            LEFT JOIN [EventManagement].[dbo].[CategoryMaster] cm
                ON sbm.CategoryId = cm.CategoryMasterId
            ORDER BY sbm.[EntryDateTime] DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        # Convert rows to list of dicts
        columns = [column[0] for column in cursor.description]
        result = [dict(zip(columns, row)) for row in rows]

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

class SponsorMasterRequest(BaseModel):
    EventMasterId: int
    SponsorName: str
    SponsorCompanyName: Optional[str] = None
    SponsorContactNo: Optional[str] = None
    SponsorEmail: Optional[str] = None
    ContactPersonName: Optional[str] = None
    ContactPersonDesignation: Optional[str] = None
    ContactPersonEmail: Optional[str] = None
    ContactPersonMobile: Optional[str] = None
    BusinessCategory: Optional[str] = None
    ApproximateBudget: Optional[float] = None
    InterestedSponsorCategory: Optional[str] = None
    EntryUserMasterId: int


@app.post("/addSponsorMaster")
def add_sponsor_master(data: SponsorMasterRequest):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # ---------------------------
        # Insert Sponsor Master
        # ---------------------------
        query = """
            INSERT INTO [EventManagement].[dbo].[SponsorMaster]
            (
                EventMasterId,
                SponsorName,
                SponsorCompanyName,
                SponsorContactNo,
                SponsorEmail,
                ContactPersonName,
                ContactPersonDesignation,
                ContactPersonEmail,
                ContactPersonMobile,
                BusinessCategory,
                ApproximateBudget,
                InterestedSponsorCategory,
                EntryUserMasterId
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

            SELECT SCOPE_IDENTITY();
        """

        cursor.execute(
            query,
            (
                data.EventMasterId,
                data.SponsorName,
                data.SponsorCompanyName,
                data.SponsorContactNo,
                data.SponsorEmail,
                data.ContactPersonName,
                data.ContactPersonDesignation,
                data.ContactPersonEmail,
                data.ContactPersonMobile,
                data.BusinessCategory,
                data.ApproximateBudget,
                data.InterestedSponsorCategory,
                data.EntryUserMasterId
            )
        )

        # Get inserted ID
        cursor.nextset()
        sponsor_master_id = cursor.fetchone()[0]

        conn.commit()

        # ---------------------------
        # Send Email (PLAIN TEXT)
        # ---------------------------
        if data.ContactPersonEmail:
            email_subject = "Sponsor Booking Confirmed"

            email_body = f"""
Dear {data.ContactPersonName},

Your sponsor booking has been successfully confirmed.

Booking Details:
Sponsor Name: {data.SponsorName}
Company Name: {data.SponsorCompanyName}
Event ID: {data.EventMasterId}
Business Category: {data.BusinessCategory}
Interested Sponsor Category: {data.InterestedSponsorCategory}
Approximate Budget: {data.ApproximateBudget}
Contact Person: {data.ContactPersonName} ({data.ContactPersonDesignation})

Thank you for partnering with us.

Regards,
Event Management Team
"""

            send_email(
                data.ContactPersonEmail,
                email_subject,
                email_body
            )

        return {
            "status": 1,
            "message": "Sponsor added successfully and email sent",
            "SponsorMasterId": int(sponsor_master_id)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.get("/getSponsorMasters")
def get_sponsor_masters():
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        query = """
            SELECT
                sm.[SponsorMasterId],
                tm.[EventName] AS EventName,
                sm.[SponsorName],
                sm.[SponsorCompanyName],
                sm.[SponsorContactNo],
                sm.[SponsorEmail],
                sm.[ContactPersonName],
                sm.[ContactPersonDesignation],
                sm.[ContactPersonEmail],
                sm.[ContactPersonMobile],
                sm.[BusinessCategory],
                sm.[ApproximateBudget],
                sm.[InterestedSponsorCategory]
            FROM [EventManagement].[dbo].[SponsorMaster] sm
            LEFT JOIN [EventManagement].[dbo].[TicketMaster] tm
                ON sm.EventMasterId = tm.TicketMasterId
            ORDER BY sm.[EntryDateTime] DESC
        """

        cursor.execute(query)
        rows = cursor.fetchall()

        columns = [col[0] for col in cursor.description]
        result = [dict(zip(columns, row)) for row in rows]

        return result  

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

class RazorpayOrderRequest(BaseModel):
    ticket_master_id: int
    ticket_classification_id: int
    name: str
    mobile_no: str
    email_id: str
    ticket_count: int


@app.post("/ticket/addTicketIssue")
def create_razorpay_order(data: RazorpayOrderRequest):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # ----------------------------------
        # Validate ticket
        # ----------------------------------
        cursor.execute("""
            SELECT TicketRate, MinimumTickets
            FROM TicketClassification
            WHERE TicketMasterId = ? AND TicketClassificationId = ?
        """, (
            data.ticket_master_id,
            data.ticket_classification_id
        ))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(400, "Invalid ticket")

        rate = float(row.TicketRate)
        min_tickets = row.MinimumTickets

        if data.ticket_count < min_tickets:
            raise HTTPException(400, f"Minimum {min_tickets} tickets required")

        total_amount = rate * data.ticket_count
        total_amount_paise = int(total_amount * 100)

        # ----------------------------------
        # Create Razorpay Order
        # ----------------------------------
        razorpay_order = razorpay_client.order.create({
            "amount": total_amount_paise,
            "currency": "INR",
            "receipt": f"TICKET_{data.mobile_no}"
        })

        # ----------------------------------
        # Insert TicketIssue with blank TransactionId
        # ----------------------------------
        cursor.execute("""
            INSERT INTO TicketIssue
            (
                TicketMasterId,
                MobileNo,
                EmailId,
                TicketCount,
                TotalAmount,
                EntryDateTime,
                Name,
                TransactionId
            )
            OUTPUT INSERTED.TicketIssueId
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.ticket_master_id,
            data.mobile_no,
            data.email_id,
            data.ticket_count,
            total_amount,
            datetime.now(),
            data.name,
            ""  
        ))

        ticket_issue_id = int(cursor.fetchone()[0])
        conn.commit()

        # ----------------------------------
        # Return both IDs for frontend
        # ----------------------------------
        return {
            "order_id": razorpay_order["id"],  
            "ticket_issue_id": ticket_issue_id 
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))

    finally:
        cursor.close()
        conn.close()

class PaymentVerificationRequest(BaseModel):
    ticket_issue_id: int
    razorpay_payment_id: str


@app.post("/ticket/verifyPayment")
def verify_payment(
    data: PaymentVerificationRequest,
    background_tasks: BackgroundTasks
):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # ---------------------------
        # Fetch TicketIssue
        # ---------------------------
        cursor.execute("""
            SELECT
                TicketMasterId,
                MobileNo,
                EmailId,
                TicketCount,
                TotalAmount,
                Name,
                TransactionId
            FROM TicketIssue
            WHERE TicketIssueId = ?
        """, data.ticket_issue_id)

        row = cursor.fetchone()
        if not row:
            return {
                "status": 0,
                "message": "TicketIssue not found"
            }

        ticket_master_id = row.TicketMasterId
        mobile_no = row.MobileNo
        email_id = row.EmailId
        ticket_count = row.TicketCount
        total_amount = row.TotalAmount
        name = row.Name
        existing_transaction = row.TransactionId
        entry_datetime = datetime.now()

        # ------------------------------
        # Prevent duplicate payment
        # ------------------------------
        if existing_transaction and existing_transaction.startswith("pay_"):
            return {
                "status": 0,
                "message": "Payment already processed"
            }

        # ----------------------------
        # Get Ticket Images 
        # ----------------------------
        cursor.execute("""
            SELECT Image5, Image6
            FROM TicketMaster
            WHERE TicketMasterId = ?
        """, ticket_master_id)

        img_row = cursor.fetchone()
        image5_path = None
        image6_path = None

        if img_row:
            if img_row.Image5:
                image5_path = os.path.join(IMAGE_BASE_PATH, img_row.Image5)
            if img_row.Image6:
                image6_path = os.path.join(IMAGE_BASE_PATH, img_row.Image6)

        # --------------------------------------------------
        # Update payment transaction ID
        # --------------------------------------------------
        cursor.execute("""
            UPDATE TicketIssue
            SET TransactionId = ?
            WHERE TicketIssueId = ?
        """, (
            data.razorpay_payment_id,
            data.ticket_issue_id
        ))

        # --------------------------------------------------
        # TicketIssueDetails + QR + PDF
        # --------------------------------------------------
        pdf_files = []

        for i in range(1, ticket_count + 1):

            cursor.execute("""
                INSERT INTO TicketIssueDetails (TicketIssueId)
                OUTPUT INSERTED.TicketIssueDetailsId
                VALUES (?)
            """, data.ticket_issue_id)

            details_id = int(cursor.fetchone()[0])

            qr_string = generate_qr_string(
                data.ticket_issue_id,
                details_id
            )

            cursor.execute("""
                UPDATE TicketIssueDetails
                SET QRCode = ?
                WHERE TicketIssueDetailsId = ?
            """, (qr_string, details_id))

            pdf_path = create_ticket_pdf(
                ticket_issue_id=data.ticket_issue_id,
                ticket_master_id=ticket_master_id,
                country_code="91",
                mobile_no=mobile_no,
                name=name,
                ticket_no=i,
                total_tickets=ticket_count,
                details_id=details_id,
                qr_code=qr_string,
                image5_path=image5_path, 
                image6_path=image6_path   
            )

            pdf_files.append(pdf_path)

        conn.commit()

        # --------------------------------------------------
        # Email + WhatsApp
        # --------------------------------------------------
        background_tasks.add_task(
            send_email_and_whatsapp,
            email_id,
            name,
            mobile_no,
            entry_datetime,
            ticket_count,
            total_amount,
            pdf_files
        )

        return {
            "status": 1,
            "message": "Payment verified and tickets issued successfully"
        }

    except Exception as e:
        conn.rollback()
        return {
            "status": 0,
            "message": f"Payment verification failed: {str(e)}"
        }

    finally:
        cursor.close()
        conn.close()

@app.get("/addTicketEnquiry")
def get_ticket_enquiry():

    return {"message": "Ticket enquiry API is working"} 

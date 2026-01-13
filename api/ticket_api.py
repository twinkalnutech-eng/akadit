from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "FastAPI working"}

@app.get("/getEventList")
def get_tickets():
    return {"getEventList": []}  

@app.get("/getEventList/{ticket_id}")
def get_ticket(ticket_id: int):
    return {"ticket_id": ticket_id, "details": "Ticket details here"}

@app.get("/events_rates/{ticket_master_id}")
def get_events_rates(ticket_master_id: int):
    return {"ticket_master_id": ticket_master_id, "rates": []}

@app.post("/addTicketEnquiry")
def save_ticket_enquiry(user_id: int):
    return {"user_id": user_id, "enquiries": []}

@app.get("/addTicketEnquiry")
def get_ticket_enquiry():
    return {"message": "Ticket enquiry API is working"}

@app.post("/addTicketIssue")
def create_ticket_issue(ticket_issue_data: dict):
    return {"message": "Ticket issue API is working"}
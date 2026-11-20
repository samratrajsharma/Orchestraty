from fastapi import FastAPI

app = FastAPI()


@app.post("/refunds")
def create_refund():
    return {}

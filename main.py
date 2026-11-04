from fastapi import FastAPI  # Import the class used to create our API.

app = FastAPI(title="Analyst Desk")  # Create the application and name it.


@app.get("/health")  # Run the function below when a GET request visits /health.
def health():
    return {"status": "ok"}  # FastAPI sends this dictionary back as JSON.
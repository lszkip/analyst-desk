# initial testing
# from fastapi import FastAPI  # Import the class used to create our API.

# app = FastAPI(title="Analyst Desk")  # Create the application and name it.


# @app.get("/health")  # Run the function below when a GET request visits /health.
# def health():
#     return {"status": "ok"}  # FastAPI sends this dictionary back as JSON.


import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import OpenAI, APIError
from pydantic import BaseModel, Field

# Read the .env file located beside main.py.
load_dotenv(Path(__file__).resolve().parent / ".env")

# Retrieve the key without putting it in our source code.
api_key = os.getenv("OPENROUTER_API_KEY")

if not api_key:
    raise RuntimeError("OPENROUTER_API_KEY is missing from .env")

# Choose a model approved for your IIT key.
MODEL = "openai/gpt-4.1-mini"

# Configure our connection to OpenRouter.
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    timeout=30.0,
    max_retries=0,
)

app = FastAPI(title="Analyst Desk")


# Describe the data someone must send to /ask.
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


# Keep our original health check.
@app.get("/health")
def health():
    return {"status": "ok"}


# Run this function when someone sends a POST request to /ask.
@app.post("/ask")
def ask(request: AskRequest):
    question = request.question.strip()  # Remove surrounding spaces.

    if not question:
        raise HTTPException(
            status_code=422,
            detail="Please enter a question.",
        )

    try:
        # Send the user's question to the AI model.
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Explain concepts clearly and briefly.",
                },
                {
                    "role": "user",
                    "content": question,
                },
            ],
            max_tokens=300,
        )
    except APIError:
        # Return a clear error without exposing provider details or secrets.
        raise HTTPException(
            status_code=502,
            detail="The AI request failed. Check the OpenRouter connection.",
        ) from None

    # Extract the answer from the first returned choice.
    answer = response.choices[0].message.content

    if not answer:
        raise HTTPException(
            status_code=502,
            detail="The AI returned no text. Please try again.",
        )

    # FastAPI converts this dictionary into a JSON response.
    return {"answer": answer, "model": MODEL}
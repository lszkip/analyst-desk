import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import OpenAI, APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from financial_tools import get_msft_annual_net_income


# Read the .env file located beside main.py.
load_dotenv(Path(__file__).resolve().parent / ".env")

# Retrieve the key without putting it in our source code.
api_key = os.getenv("OPENROUTER_API_KEY")

if not api_key:
    raise RuntimeError("OPENROUTER_API_KEY is missing from .env")

# Use a model approved for your IIT key.
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


# Validate the arguments the AI requests for our financial tool.
class NetIncomeArguments(BaseModel):
    # Reject unexpected arguments and require an actual integer.
    model_config = ConfigDict(extra="forbid", strict=True)

    year: int = Field(
        ge=1900,
        le=2100,
        description="Microsoft fiscal year, for example 2025.",
    )

# Describe our Python function to the AI.
# This description does not execute the function.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_msft_annual_net_income",
            "description": (
                "Retrieve Microsoft's annual net income from locally "
                "downloaded SEC data for a specified fiscal year. "
                "Microsoft's fiscal year ends on June 30. "
                "Only supports Microsoft and annual net income."
            ),
            "parameters": NetIncomeArguments.model_json_schema(),
        },
    }
]

# Provide a simple endpoint to check that the application is running.
@app.get("/health")
def health():
    return {"status": "ok"}

# Run this function when someone sends a POST request to /ask.
@app.post("/ask")
def ask(request: AskRequest):
    # Remove surrounding spaces from the question.
    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=422,
            detail="Please enter a question.",
        )

    # Start a new conversation for this request.
    messages = [
        {
            "role": "system",
            "content": (
                "You are Analyst Desk. Explain clearly and briefly. "
                "For Microsoft annual net income questions, use the tool. "
                "Interpret a specified year as Microsoft's fiscal year "
                "and clearly state this in your answer. "
                "If no year is specified, ask which fiscal year is wanted. "
                "Only report financial figures returned by the tool; "
                "never fill missing figures from memory. "
                "If the tool reports missing data, a conflict, or an error, "
                "explain that limitation without inventing a value. "
                "Include the currency, reporting period, and filing "
                "identifier when reporting a retrieved figure. "
                "Other companies and financial metrics are not supported "
                "yet. You may explain general financial concepts."
            ),
        },
        {
            "role": "user",
            "content": question,
        },
    ]

    # Keep the Python results so we can inspect them in the response.
    evidence = []

    try:
        # First AI request: answer directly or request a tool.
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=600,
        )

        if not response.choices:
            raise HTTPException(
                status_code=502,
                detail="The AI returned no response.",
            )

        message = response.choices[0].message

        # If the AI requested tools, validate and execute those requests.
        if message.tool_calls:
            # Limit the amount of work performed for one question.
            if len(message.tool_calls) > 3:
                raise HTTPException(
                    status_code=422,
                    detail="Please request no more than three fiscal years.",
                )

            # Add the AI's tool requests to the conversation.
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        call.model_dump(exclude_none=True)
                        for call in message.tool_calls
                    ],
                }
            )

            for call in message.tool_calls:
                # Only allow the function we explicitly support.
                if call.function.name != "get_msft_annual_net_income":
                    raise HTTPException(
                        status_code=502,
                        detail="The AI requested an unsupported tool.",
                    )

                # Check the AI's arguments before running our function.
                try:
                    arguments = NetIncomeArguments.model_validate_json(
                        call.function.arguments
                    )
                except ValidationError:
                    raise HTTPException(
                        status_code=502,
                        detail="The AI supplied invalid tool arguments.",
                    ) from None

                # Run ordinary Python to retrieve the financial data.
                try:
                    result = get_msft_annual_net_income(
                        year=arguments.year
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "Could not read the local financial data. "
                            "Check the downloaded SEC file."
                        ),
                    ) from None

                # Record which tool ran, its arguments, and its result.
                evidence.append(
                    {
                        "tool": call.function.name,
                        "arguments": arguments.model_dump(),
                        "result": result,
                    }
                )

                # Send the result back with its matching tool-call ID.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result),
                    }
                )

            # Second AI request: explain the retrieved results.
            # Prevent additional tool calls in this first version.
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="none",
                max_tokens=600,
            )

            if not response.choices:
                raise HTTPException(
                    status_code=502,
                    detail="The AI returned no explanation.",
                )

            message = response.choices[0].message

        # Extract the final text answer.
        answer = message.content

        if not answer:
            raise HTTPException(
                status_code=502,
                detail="The AI returned no text. Please try again.",
            )

    except APIError:
        # Avoid exposing provider details or secrets to the caller.
        raise HTTPException(
            status_code=502,
            detail="The AI request failed. Check the OpenRouter connection.",
        ) from None

    # FastAPI converts this dictionary into a JSON response.
    return {
        "answer": answer,
        "model": MODEL,
        "evidence": evidence,
    }
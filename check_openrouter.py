import os  # Lets us read environment variables.
from pathlib import Path  # Helps us locate files.
from dotenv import load_dotenv  # Reads settings from our .env file.
from openai import OpenAI  # Provides the client we use to contact OpenRouter.

# Find .env in the same folder as this Python script.
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

# Read the key without writing it into our Python code.
api_key = os.getenv("OPENROUTER_API_KEY")

# Stop with a clear message if the key is missing.
if not api_key:
    raise SystemExit("Add OPENROUTER_API_KEY to your .env file and save it.")

# Configure the client to send requests to OpenRouter.
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
    timeout=30.0,  # Limit how long we wait for network operations.
    max_retries=0,  # Keep this first test to one attempt.
)

print("Sending a question to OpenRouter...")

# Send one message to the selected AI model.
response = client.chat.completions.create(
    model="openai/gpt-4.1-mini",
    messages=[
        {
            "role": "user",
            "content": "Explain what a company's annual report is in two simple sentences.",
        }
    ],
    max_tokens=150,  # Limit the length of the generated answer.
)

# Extract and display the text from the first reply.
print("\nAI reply:")
print(response.choices[0].message.content)
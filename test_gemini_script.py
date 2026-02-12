
import os
from google import genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("Error: GEMINI_API_KEY not found in .env file.")
    exit(1)

# Initialize the client with the new SDK
client = genai.Client(api_key=api_key)

print("Sending prompt to Gemini...")
try:
    # effective model name might be 'gemini-2.0-flash' or similar depending on availability
    # sticking to user's requested model or a standard one
    response = client.models.generate_content(
        model='gemini-3.5-flash', 
        contents="Explain how AI works to a 5 year old. Also Can you generate images?"
    )
    print("\nResponse from Gemini:")
    print(response.text)
except Exception as e:
    print(f"\nError contacting Gemini API: {e}")

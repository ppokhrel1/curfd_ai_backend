
import os
from google import genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY not found in .env file.")
    exit(1)

# Initialize Client
client = genai.Client(api_key=api_key)

print("Fetching available models...")
try:
    for model in client.models.list():
        print(f"Model: {model.name}")
        print(f"  Display Name: {model.display_name}")
        print(f"  Input Token Limit: {model.input_token_limit}")
        print(f"  Output Token Limit: {model.output_token_limit}")
        print("-" * 30)
        
    print("\nNote: 'Available and used' tokens (quota) are not provided by the list_models endpoint.")
    print("Please check your Google Cloud Console or AI Studio for quota usage.")
    
except Exception as e:
    print(f"Error listing models: {e}")

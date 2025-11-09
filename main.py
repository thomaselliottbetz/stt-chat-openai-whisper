"""
AWS Lambda function for speech-to-text transcription using Whisper.

This Lambda function is triggered by S3 uploads of audio files. It:
1. Downloads the audio file from S3
2. Transcribes it using OpenAI's Whisper model
3. Uploads the transcription to an output S3 bucket
4. Sends the transcription to the FastAPI backend via callback

The Lambda uses CPU-only compute, so the 'small' Whisper model is used
for faster inference while maintaining good accuracy.
"""

import json
import os
from datetime import datetime

import boto3
import requests
import whisper

# Initialize S3 client
s3 = boto3.client("s3")

# Configuration from environment variables
OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET")
if not OUTPUT_BUCKET:
    raise ValueError("OUTPUT_BUCKET environment variable must be set")

CALLBACK_URL = os.getenv("CALLBACK_URL")
if not CALLBACK_URL:
    raise ValueError("CALLBACK_URL environment variable must be set")

SHARED_SECRET = os.getenv("SHARED_SECRET")
if not SHARED_SECRET:
    raise ValueError("SHARED_SECRET environment variable must be set")

# Load Whisper model (loaded once at cold start)
model = whisper.load_model("small", download_root="/opt/models")


def handler(event, context):
    """
    Lambda handler for S3-triggered audio transcription.

    Args:
        event: S3 event containing bucket and object key
        context: Lambda context

    Returns:
        dict: Status code and transcription result
    """
    # Extract S3 bucket and object key from event
    record = event["Records"][0]
    input_bucket = record["s3"]["bucket"]["name"]
    input_key = record["s3"]["object"]["key"]
    username = input_key.split("/")[0]

    # Download audio file from S3
    local_audio_path = f"/tmp/{os.path.basename(input_key)}"
    s3.download_file(input_bucket, input_key, local_audio_path)

    # Transcribe audio file
    result = model.transcribe(local_audio_path)

    # Create timestamped output key
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    base_filename = os.path.splitext(os.path.basename(input_key))[0]
    output_key = f"{timestamp}_{base_filename}.json"

    # Upload transcription to output bucket
    transcription_json = json.dumps(result)
    s3.put_object(Bucket=OUTPUT_BUCKET, Key=output_key, Body=transcription_json)

    # Send transcription to FastAPI backend for real-time delivery
    try:
        response = requests.post(
            CALLBACK_URL,
            json={
                "secret": SHARED_SECRET,
                "message": {
                    "sender": username,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "text": result["text"],
                    "audio_key": input_key,
                },
            },
            timeout=5,
        )
        response.raise_for_status()
    except Exception as e:
        # Log error but don't fail the Lambda
        # The transcription is still saved to S3
        print(f"Error posting to FastAPI server: {e}")

    return {
        "statusCode": 200,
        "body": json.dumps({"output_key": output_key, "transcription": result}),
    }



# Speech-To-Text Chat Application

A real-time chat application with OpenAI Whisper model speech-to-text transcription, designed for use between an admin and invited users.

## Features

- **Real-time Messaging**: WebSocket-based chat with instant message delivery
- **Speech-to-Text**: Voice messages transcribed using OpenAI's Whisper model via AWS Lambda
- **User Authentication**: Secure login with bcrypt password hashing
- **Invite-Only Registration**: Controlled access via invitation codes
- **Admin Interface**: Admin can manage multiple one-on-one conversations
- **Message History**: Infinite scroll pagination for chat history
- **Read Status**: Track when chats were last viewed

## Architecture

### Frontend
- **HTML/CSS/JavaScript**: Vanilla JavaScript frontend with modern CSS
- **WebSocket**: Real-time bidirectional communication
- **MediaRecorder API**: Browser-based audio recording

### Backend
- **FastAPI**: Python web framework for REST API and WebSocket server
- **SQLite**: Lightweight database for users, chats, and messages
- **SQLAlchemy Core**: Database query interface

### Infrastructure
- **AWS Lambda**: Serverless speech-to-text transcription using Whisper
- **AWS S3**: Audio file storage and transcription output
- **Caddy**: Reverse proxy and TLS termination
- **Docker**: Containerized Lambda function

## Project Structure

```
.
├── app.py                 # FastAPI backend server
├── main.py                # AWS Lambda function for STT
├── index.html             # Frontend HTML
├── main.js                # Frontend JavaScript
├── main.css               # Frontend styles
├── schema.sql             # Database schema (best practices)
├── cleanup_sessions.sql   # Session cleanup script
├── Dockerfile             # Lambda container definition
├── Caddyfile              # Caddy web server configuration
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

## Setup

### Prerequisites

- Python 3.12+
- Node.js (for development)
- AWS Account (for Lambda and S3)
- SQLite 3

### Environment Variables

Create a `fastapi.env` file with:

```env
DATABASE_PATH=db/<your-database>
SHARED_SECRET=<your-strong-secret>
ADMIN_USERNAME=<your-admin-username>
INPUT_BUCKET=<your-input-bucket-name>
OUTPUT_BUCKET=<your-output-bucket-name>
```

For Lambda function, set (all required):
- `SHARED_SECRET`: Must match FastAPI backend
- `OUTPUT_BUCKET`: S3 bucket for transcription output
- `CALLBACK_URL`: FastAPI callback endpoint URL

### Database Setup

1. Create database from schema:
   ```bash
   sqlite3 app.db < schema.sql
   ```

2. Or migrate existing database:
   ```bash
   sqlite3 app.db < migrate_schema.sql
   ```

### Running the Application

1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Start FastAPI server:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```

3. Configure Caddy to reverse proxy to FastAPI (see `Caddyfile`)

4. Deploy Lambda function (see `Dockerfile`)

## Database Schema

The database uses SQLite with the following tables:

- **users**: User accounts with hashed passwords
- **registration_invitations**: Invite codes for user registration
- **chats**: Chat conversations (currently all one-on-one)
- **chat_participants**: Many-to-many relationship between users and chats
- **messages**: Chat messages with timestamps
- **sessions**: User authentication sessions with expiration
- **chat_reads**: Track when users last viewed each chat

See `schema.sql` for the complete schema with indexes and constraints.

## Security Features

- **Server-side Authorization**: All security checks enforced on backend
- **Password Hashing**: bcrypt with secure defaults
- **Session Management**: Token-based authentication with expiration
- **Invite-Only Registration**: Prevents unauthorized signups
- **Input Validation**: Username and message length constraints
- **SQL Injection Protection**: Parameterized queries throughout

## Performance Optimizations

- **Database Indexes**: Comprehensive indexes for common query patterns
- **WebSocket Keepalive**: Prevents connection timeouts
- **Infinite Scroll**: Efficient message pagination
- **Lambda Cold Start**: Model loaded once at container initialization

## Maintenance

### Session Cleanup

Run periodically to remove expired sessions:

```bash
sqlite3 app.db < cleanup_sessions.sql
```

Or set up a cron job:

```bash
0 2 * * * sqlite3 /path/to/app.db < /path/to/cleanup_sessions.sql
```

## Development Notes

- All chats are currently one-on-one between regular users and admin
- Speech-to-text inference uses CPU-only Lambda (small Whisper model for speed)
- Frontend uses vanilla JavaScript (no frameworks)
- Database migrations preserve existing data

## License

© 2025 Thomas Betz — MIT License

/**
 * Database Schema for Chat Application
 * 
 * This schema defines the IMPROVED database structure with best practices for a
 * personal chat application where all chats are one-on-one conversations between
 * regular users (friends and family) and the admin.
 * 
 * Design Notes:
 * - Sessions expire after 30 days (configurable via expires_at)
 * - Usernames are limited to 3-50 characters
 * - Messages are limited to 10,000 characters
 * - All chats are currently one-on-one (is_group = 0)
 * - Foreign keys cascade on delete for data integrity
 * - Comprehensive indexes for performance optimization
 * 
 * To migrate an existing database to this schema, see migrate_schema.sql
 */

-- Users table: Stores user accounts with hashed passwords
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL CHECK(LENGTH(username) >= 3 AND LENGTH(username) <= 50),
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Registration invitations: Controls access to user registration
CREATE TABLE registration_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used BOOLEAN DEFAULT 0 NOT NULL
);

-- Chats table: Represents chat conversations
-- Note: Currently all chats are one-on-one (is_group = 0) between regular users and admin
CREATE TABLE chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_group BOOLEAN DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chat participants: Many-to-many relationship between users and chats
CREATE TABLE chat_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(chat_id, user_id)
);

-- Messages table: Stores all chat messages
-- Note: Message text is limited to 10,000 characters to prevent abuse
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    text TEXT NOT NULL CHECK(LENGTH(text) > 0 AND LENGTH(text) <= 10000),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL DEFAULT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Sessions table: Manages user authentication sessions
-- Sessions expire after 30 days (2592000 seconds)
-- Expired sessions should be cleaned up periodically
CREATE TABLE sessions (
    user_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP DEFAULT (datetime('now', '+30 days')) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Chat reads table: Tracks when users last viewed each chat
CREATE TABLE chat_reads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, user_id),
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Indexes for performance optimization
-- Based on actual query patterns in the application

-- Messages: Most queries filter by chat_id and order by id or timestamp
CREATE INDEX idx_messages_chat_id ON messages(chat_id);
-- Composite index for pagination: WHERE chat_id = ? AND id < ? ORDER BY id DESC
CREATE INDEX idx_messages_chat_id_id ON messages(chat_id, id DESC);
-- Composite index for chat list ordering: ORDER BY timestamp DESC per chat
CREATE INDEX idx_messages_chat_timestamp ON messages(chat_id, timestamp DESC);
-- Composite index for unread count queries: WHERE chat_id = ? AND sender_id != ?
CREATE INDEX idx_messages_chat_sender ON messages(chat_id, sender_id);
-- Index for soft-deleted message queries (for archiving)
CREATE INDEX idx_messages_deleted_at ON messages(deleted_at) WHERE deleted_at IS NOT NULL;

-- Chat participants: Frequently queried by chat_id or user_id
CREATE INDEX idx_chat_participants_chat_id ON chat_participants(chat_id);
CREATE INDEX idx_chat_participants_user_id ON chat_participants(user_id);
-- Composite index for participant lookups: WHERE chat_id = ? AND user_id = ?
CREATE INDEX idx_chat_participants_chat_user ON chat_participants(chat_id, user_id);

-- Sessions: Token lookups are critical for authentication
CREATE INDEX idx_sessions_token ON sessions(token);
-- User session lookups for cleanup/management
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
-- Index for session cleanup: DELETE FROM sessions WHERE expires_at < datetime('now')
CREATE INDEX idx_sessions_expires_at ON sessions(expires_at);

-- Chat reads: Lookups by chat_id and user_id for read status
CREATE INDEX idx_chat_reads_chat_user ON chat_reads(chat_id, user_id);
-- Index for finding max viewed_at per chat/user
CREATE INDEX idx_chat_reads_user_chat ON chat_reads(user_id, chat_id, viewed_at DESC);

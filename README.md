# P2P Chat System

A peer-to-peer chat application with encryption and topic-based messaging.

## Features

- **Secure P2P Messaging**: Direct communication between peers
- **Encryption**: All messages are encrypted
- **Discovery Server**: Helps peers find each other
- **Topic Subscription**: Subscribe to topics and receive broadcasts
- **User Management**: Block, unblock, and mute users
- **Message Storage**: Local database for chat history

## Requirements

- Python 3.7+
- Cryptography package

```bash
pip install cryptography
```

## Getting Started

### 1. Start the Discovery Server

```bash
python server.py
```

Optional parameters:
- `--host`: Server address (default: localhost)
- `--port`: Server port (default: 5000)

### 2. Start Chat Clients

```bash
python chat_cli.py --port 5001
```

Options:
- `--port`: Client port (default: auto-assigned)
- `--discovery-host`: Discovery server address (default: localhost)
- `--discovery-port`: Discovery server port (default: 5000)

## Commands

- `peers` - Show connected peers
- `<peer_id> <message>` - Send message to a peer
- `block <peer_id>` - Block a peer
- `unblock <peer_id>` - Unblock a peer
- `mute <peer_id> <seconds>` - Temporarily mute a peer
- `unmute <peer_id>` - Unmute a peer
- `subscribe <topic>` - Subscribe to a topic
- `unsubscribe <topic>` - Unsubscribe from a topic
- `publish <topic> <message>` - Publish message to a topic
- `muted` - Show all muted peers
- `quit` - Exit the program

## Testing

Run unit tests:
```bash
python test_peer.py
```

Run integration test:
```bash
python integration_test.py
```

Run stress test:
```bash
python stress_test.py
```

## Quick Test Example

To test the chat system with two clients:

1. Start the discovery server in one terminal:
```bash
python server.py
```

2. Start the first chat client in a second terminal:
```bash
python chat_cli.py --port 5001
```

3. Start the second chat client in a third terminal:
```bash
python chat_cli.py --port 5002
```

4. In each client, type `peers` to see connected peers
5. To send a message, type the peer ID followed by your message

## Troubleshooting

If you get "Address already in use" error:

1. Use a different port:
   ```bash
   python server.py --port 5001
   ```

2. Find and kill the process using the port:
   ```bash
   # macOS/Linux
   sudo lsof -i :5000
   kill <PID>
   
   # Windows
   netstat -ano | findstr :5000
   taskkill /PID <PID> /F
   ```
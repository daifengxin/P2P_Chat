import socket
import threading
import json
import time
import hashlib
import sqlite3 as sqlite
from typing import Dict, Optional, Set
from datetime import datetime
import logging
from cryptography.fernet import Fernet
import os
from cryptography.hazmat.primitives.asymmetric import dh
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class P2PPeer:
    def __init__(self, host: str = 'localhost', port: int = 0, debug=False):
        """Initialize P2P node
        
        Args:
            host: Local listening address
            port: Local listening port (0 means auto-assign)
        """
        self.peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Add socket reuse option
        self.peer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            self.peer_socket.bind((host, port))
        except OSError as e:
            if e.errno == 48:  # Address already in use
                print(f"Port {port} is already in use, trying another port...")
                self.peer_socket.bind((host, 0))
            else:
                raise e
            
        self.peer_socket.listen(5)
        
        self.host = host
        self.port = self.peer_socket.getsockname()[1]  # Get actual port
        
        # Use fixed information to generate peer_id for consistency
        self.peer_id = hashlib.sha256(f"{host}:{self.port}".encode()).hexdigest()[:12]
        self.discovery_server: Optional[socket.socket] = None
        self.connected_peers: Dict[str, socket.socket] = {}
        self.message_queue = []
        self.blocked_users: Set[str] = set()
        self.muted_users: Dict[str, float] = {}  # peer_id -> mute_until_time
        self.session_keys: Dict[str, bytes] = {}
        self.subscriptions = set()  # Topics subscribed by self
        self.peer_subscriptions: Dict[str, Set[str]] = {}  # peer_id -> set of topics subscribed by that peer
        
        # Initialize database
        self.init_database()
        
        # Start listening thread
        self.listen_thread = threading.Thread(target=self.listen_for_connections)
        self.listen_thread.daemon = True
        self.listen_thread.start()
        
        self.debug = debug
        if self.debug:
            print(f"Debug info: {message}")
        
    def init_database(self):
        """Initialize local SQLite database"""
        # Create unique database filename using peer_id
        db_file = f'peer_messages_{self.peer_id}.db'
        
        # Try to delete old database file - only our own
        try:
            if os.path.exists(db_file):
                os.remove(db_file)
        except:
            pass
        
        self.conn = sqlite.connect(db_file, check_same_thread=False)
        cursor = self.conn.cursor()
        
        # Create tables
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id TEXT,
            receiver_id TEXT,
            content TEXT,
            timestamp DATETIME,
            status TEXT,
            message_type TEXT
        )
        ''')
        self.conn.commit()
        
    def connect_to_discovery_server(self, server_host: str, server_port: int):
        """Connect to discovery server"""
        try:
            print(f"\nConnecting to discovery server at {server_host}:{server_port}")
            print(f"My listening port is: {self.port}")
            print(f"My peer_id is: {self.peer_id}")
            
            self.discovery_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.discovery_server.connect((server_host, server_port))
            
            # Send discovery request with listen port
            self.discovery_server.send(json.dumps({
                'type': 'discovery',
                'listen_port': self.port
            }).encode('utf-8'))
            
            # Start message handling thread
            discovery_thread = threading.Thread(target=self.handle_discovery_messages)
            discovery_thread.daemon = True
            discovery_thread.start()
            
        except Exception as e:
            logger.error(f"Failed to connect to discovery server: {e}")
            raise
            
    def send_discovery_request(self):
        """Send node discovery request"""
        if self.discovery_server:
            self.discovery_server.send(json.dumps({
                'type': 'discovery'
            }).encode('utf-8'))
            
    def handle_discovery_messages(self):
        """Handle messages from discovery server"""
        while True:
            try:
                if not self.discovery_server:
                    break
                    
                message = self.discovery_server.recv(1024).decode('utf-8')
                if not message:
                    break
                    
                msg_data = json.loads(message)
                msg_type = msg_data.get('type', '')
                
                print(f"Received message from discovery server: {msg_type}")
                
                # 简化的消息类型处理
                if msg_type == 'discovery_response':
                    self.handle_discovery_response(msg_data['peers'])
                elif msg_type == 'peer_lookup_response':
                    # 处理特定peer查询
                    peer_id = msg_data.get('peer_id')
                    peer_info = msg_data.get('peer_info')
                    error = msg_data.get('error')
                    
                    print(f"Received peer lookup response: peer_id={peer_id}, peer_info={peer_info}, error={error}")
                    
                    if peer_id and peer_info:
                        peer_addr, peer_port = peer_info.get('addr'), peer_info.get('port')
                        if peer_addr and peer_port:
                            # 尝试连接，无论当前连接状态
                            print(f"Attempting to reconnect to peer {peer_id} at {peer_addr}:{peer_port}")
                            self.connect_to_peer(peer_id, peer_addr, peer_port)
                        else:
                            print(f"peer_info is missing addr or port: {peer_info}")
                    else:
                        print(f"Peer lookup failed: {error or 'Unknown error'}")
                
            except Exception as e:
                logger.error(f"Error handling discovery server message: {e}")
                break
                
    def handle_discovery_response(self, peers: dict):
        """Handle discovery response"""
        print("\nAvailable peers:", list(peers.keys()))
        print("> ", end='', flush=True)
        
        for peer_id, peer_info in peers.items():
            if (peer_id not in self.connected_peers and 
                peer_id not in self.blocked_users):
                print(f"Attempting to connect to new peer {peer_id}")
                # Get actual listen port from peer_info
                peer_addr, peer_port = peer_info['addr'], peer_info['port']
                self.connect_to_peer(peer_id, peer_addr, peer_port)
                
    def connect_to_peer(self, peer_id: str, peer_addr: str, target_port: int):
        """Connect to other nodes"""
        try:
            print(f"\nAttempting to connect to peer {peer_id} at {peer_addr}:{target_port}")
            
            # Create new socket connection
            peer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            peer_socket.settimeout(30)  # Increase timeout time
            
            # Connect to peer's listen port
            peer_socket.connect((peer_addr, target_port))
            print(f"Socket connection established with {peer_id}")
            
            # Send identity information
            peer_socket.send(json.dumps({
                'type': 'peer_info',
                'peer_id': self.peer_id,
                'port': self.port,
                'sender_id': self.peer_id,
                'receiver_id': peer_id,
                'content': f"Peer info from {self.peer_id}",
                'timestamp': datetime.now().isoformat()
            }).encode('utf-8'))
            
            # Store connection
            self.connected_peers[peer_id] = peer_socket
            print(f"Successfully added peer {peer_id} to connected_peers")
            
            # Start message handling thread
            peer_thread = threading.Thread(
                target=self.handle_peer_messages,
                args=(peer_id, peer_socket)
            )
            peer_thread.daemon = True
            peer_thread.start()
            print(f"Started message handling thread for peer {peer_id}")
            
        except Exception as e:
            print(f"Failed to connect to peer {peer_id}: {e}")
            if peer_id in self.connected_peers:
                del self.connected_peers[peer_id]
            
    def listen_for_connections(self):
        """Listen for connections from other nodes"""
        while True:
            try:
                # Check if socket is closed
                if self.peer_socket._closed:
                    break
                    
                try:
                    client_socket, address = self.peer_socket.accept()
                except socket.error as e:
                    if e.errno == 9:  # Bad file descriptor
                        break
                    logger.error(f"Error accepting connection: {e}")
                    continue
                    
                peer_id = hashlib.sha256(f"{address[0]}:{time.time()}".encode()).hexdigest()[:12]
                
                if peer_id not in self.blocked_users:
                    self.connected_peers[peer_id] = client_socket
                    
                    peer_thread = threading.Thread(
                        target=self.handle_peer_messages,
                        args=(peer_id, client_socket)
                    )
                    peer_thread.daemon = True
                    peer_thread.start()
                else:
                    client_socket.close()
                    
            except Exception as e:
                if isinstance(e, socket.error) and e.errno == 9:
                    break
                logger.error(f"Error in listen_for_connections: {e}")
                time.sleep(0.1)  # Avoid CPU overload
                
    def handle_peer_messages(self, peer_id: str, peer_socket: socket.socket):
        """Handle messages from other nodes"""
        last_activity = time.time()
        
        # First perform key exchange
        try:
            self.exchange_keys(peer_id, peer_socket)
        except Exception as e:
            logger.error(f"Key exchange failed with peer {peer_id}: {e}")
            self.remove_peer(peer_id)
            return
        
        while True:
            try:
                peer_socket.settimeout(5)
                
                try:
                    message = peer_socket.recv(1024).decode('utf-8')
                    if not message:
                        break
                    last_activity = time.time()
                    
                except socket.timeout:
                    # Handle timeout, send heartbeat
                    if time.time() - last_activity > 10:
                        try:
                            peer_socket.send(json.dumps({
                                'type': 'heartbeat',
                                'peer_id': self.peer_id
                            }).encode('utf-8'))
                            last_activity = time.time()
                        except:
                            break
                    continue
                except (ConnectionError, socket.error) as e:
                    # Explicitly handle connection errors
                    if e.errno == 9:  # Bad file descriptor
                        logger.info(f"Socket for peer {peer_id} was closed")
                    else:
                        logger.error(f"Connection error with peer {peer_id}: {e}")
                    break
                    
                msg_data = json.loads(message)
                
                # Only print all messages in debug mode
                if self.debug:
                    print(f"Received message from {peer_id}: {msg_data}")
                
                # Handle heartbeat messages - no longer printing
                if msg_data.get('type') == 'heartbeat':
                    last_activity = time.time()
                    continue
                    
                if msg_data.get('type') == 'peer_info':
                    # Update peer information
                    remote_peer_id = msg_data.get('peer_id')
                    if remote_peer_id and remote_peer_id != peer_id:
                        print(f"Updating peer ID from {peer_id} to {remote_peer_id}")
                        if peer_id in self.connected_peers:
                            connection = self.connected_peers.pop(peer_id)
                            self.connected_peers[remote_peer_id] = connection
                            peer_id = remote_peer_id
                            print(f"Successfully updated peer ID. Connected peers: {self.connected_peers.keys()}")
                
                # 检查发送者是否被阻止 - 如果被阻止，忽略消息但保持连接
                if peer_id in self.blocked_users:
                    print(f"Ignoring message from blocked user {peer_id}")
                    continue
                
                # Check if sender is muted, if so skip message processing
                if self.is_user_muted(peer_id):
                    logger.info(f"Ignoring message from muted peer {peer_id}")
                    continue
                
                # Only display actual messages
                if msg_data.get('type') not in ['peer_info', 'heartbeat']:
                    # Check if message is encrypted, decrypt if necessary
                    content = msg_data.get('content', '')
                    if msg_data.get('encrypted', False):
                        try:
                            # If no key exists for this node, create one
                            if peer_id not in self.session_keys:
                                self.session_keys[peer_id] = Fernet.generate_key()
                                logger.info(f"Generated new encryption key for node {peer_id}")
                            
                            content = self.decrypt_message(content, peer_id)
                            # Update message content with decrypted content
                            msg_data['content'] = content
                        except Exception as e:
                            logger.error(f"Failed to decrypt message: {e}")
                    
                    # Handle topic subscription messages
                    if msg_data.get('type') == 'topic_subscription':
                        try:
                            subscription_data = json.loads(content)
                            topic = subscription_data.get('topic')
                            action = subscription_data.get('action')
                            
                            if topic:
                                if action == 'subscribe':
                                    # Record peer subscription
                                    if peer_id not in self.peer_subscriptions:
                                        self.peer_subscriptions[peer_id] = set()
                                    self.peer_subscriptions[peer_id].add(topic)
                                    logger.info(f"Peer {peer_id} subscribed to topic: {topic}")
                                elif action == 'unsubscribe':
                                    # Handle unsubscription
                                    if peer_id in self.peer_subscriptions and topic in self.peer_subscriptions[peer_id]:
                                        self.peer_subscriptions[peer_id].remove(topic)
                                        logger.info(f"Peer {peer_id} unsubscribed from topic: {topic}")
                        except Exception as e:
                            logger.error(f"Error processing topic subscription: {e}")
                    
                    # Handle topic messages
                    elif msg_data.get('type') == 'topic_message':
                        try:
                            topic_data = json.loads(content)
                            topic = topic_data.get('topic')
                            topic_content = topic_data.get('content')
                            
                            # Check if self has subscribed to this topic
                            if topic and topic in self.subscriptions:
                                # If message callback exists, call it
                                if hasattr(self, 'message_callback'):
                                    self.message_callback(peer_id, 'topic', f"[Topic: {topic}] {topic_content}")
                        except Exception as e:
                            logger.error(f"Error processing topic message: {e}")
                    
                    self.store_message(msg_data)
                    
                    # Call callback function if exists
                    if hasattr(self, 'message_callback') and msg_data.get('type') not in ['topic_subscription', 'topic_message']:
                        self.message_callback(peer_id, msg_data.get('type'), content)
                
            except Exception as e:
                logger.error(f"Error handling message from peer {peer_id}: {e}")
                break
                
        self.remove_peer(peer_id)
        
    def send_message(self, to_peer: str, content: str, message_type: str = 'text'):
        """Send encrypted message to specified node"""
        # 首先检查接收者是否被阻止
        if to_peer in self.blocked_users:
            print(f"Cannot send message: user {to_peer} is blocked")
            return False
        
        # Encrypt the content
        encrypted_content = self.encrypt_message(content, to_peer)
        
        message = {
            'sender_id': self.peer_id,
            'receiver_id': to_peer,
            'content': encrypted_content,
            'timestamp': datetime.now().isoformat(),
            'type': message_type,
            'encrypted': True
        }
        
        if to_peer in self.connected_peers:
            try:
                self.connected_peers[to_peer].send(json.dumps(message).encode('utf-8'))
                self.store_message(message, status='sent')
                return True
            except:
                self.message_queue.append(message)
                self.store_message(message, status='queued')
                return False
        else:
            self.message_queue.append(message)
            self.store_message(message, status='queued')
            return False
            
    def store_message(self, message: dict, status: str = 'received'):
        """Store message in local database"""
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO messages (sender_id, receiver_id, content, timestamp, status, message_type)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            message['sender_id'],
            message['receiver_id'],
            message['content'],
            message['timestamp'],
            status,
            message.get('type', 'text')
        ))
        self.conn.commit()
        
    def send_queued_messages(self, peer_id: str):
        """Send queued messages"""
        queued_messages = [m for m in self.message_queue if m['receiver_id'] == peer_id]
        for message in queued_messages:
            try:
                self.connected_peers[peer_id].send(json.dumps(message).encode('utf-8'))
                self.message_queue.remove(message)
                self.store_message(message, status='sent')
            except:
                continue
                
    def remove_peer(self, peer_id: str):
        """Remove disconnected node"""
        if peer_id in self.connected_peers:
            try:
                self.connected_peers[peer_id].shutdown(socket.SHUT_RDWR)
            except:
                pass
            self.connected_peers[peer_id].close()
            del self.connected_peers[peer_id]
            
    def close(self):
        """Close connections and clean up resources"""
        # Mark as closing
        self._closing = True
        
        if self.discovery_server:
            try:
                self.discovery_server.shutdown(socket.SHUT_RDWR)
                self.discovery_server.close()
            except:
                pass
        
        # Use list copy to avoid dictionary modification during iteration
        for peer_socket in list(self.connected_peers.values()):
            try:
                peer_socket.shutdown(socket.SHUT_RDWR)
                peer_socket.close()
            except:
                pass
        self.connected_peers.clear()
        
        try:
            self.peer_socket.shutdown(socket.SHUT_RDWR)
            self.peer_socket.close()
        except:
            pass
        
        if hasattr(self, 'conn'):
            self.conn.close()

    def send_greeting(self, to_peer: str):
        """Send greeting message"""
        self.send_message(to_peer, "GREETING", message_type='greeting')
        
    def block_user(self, peer_id: str):
        """Block user - only locally mark as blocked without disconnecting
        
        Args:
            peer_id: ID of user to block
        """
        # Simply add to blocked users list without disconnecting
        self.blocked_users.add(peer_id)
        print(f"User {peer_id} has been locally blocked, but network connection maintained")
        
    def unblock_user(self, peer_id: str):
        """Unblock user - simply remove from blocked list
        
        Args:
            peer_id: ID of user to unblock
        """
        # Remove from blocked users list
        self.blocked_users.discard(peer_id)
        print(f"User {peer_id} has been unblocked")
        
    def mute_user(self, peer_id: str, duration: int):
        """Temporarily mute user
        
        Args:
            peer_id: ID of user to mute
            duration: Duration in seconds
        """
        self.muted_users[peer_id] = time.time() + duration
        
    def unmute_user(self, peer_id: str):
        """Unmute user"""
        if peer_id in self.muted_users:
            del self.muted_users[peer_id]

    def get_connected_peers(self) -> list:
        """Get list of all connected peers"""
        return list(self.connected_peers.keys())

    def encrypt_message(self, message: str, peer_id: str) -> str:
        """Encrypt message with session-specific key"""
        if peer_id not in self.session_keys:
            self.session_keys[peer_id] = Fernet.generate_key()
        
        f = Fernet(self.session_keys[peer_id])
        return f.encrypt(message.encode()).decode()

    def decrypt_message(self, encrypted_message: str, peer_id: str) -> str:
        """Decrypt message using session-specific key"""
        if peer_id not in self.session_keys:
            # If no key exists, create one
            self.session_keys[peer_id] = Fernet.generate_key()
            logger.info(f"Created new decryption key for node {peer_id}")
        
        try:
            f = Fernet(self.session_keys[peer_id])
            return f.decrypt(encrypted_message.encode()).decode()
        except Exception as e:
            # If decryption fails, key might not match, try generating new key
            logger.warning(f"Decryption failed, trying to generate new key: {e}")
            self.session_keys[peer_id] = Fernet.generate_key()
            # Return original encrypted text with hint
            return f"[Unable to decrypt] {encrypted_message[:20]}..."

    def authenticate_peer(self, peer_id: str) -> bool:
        """Basic peer authentication mechanism"""
        try:
            # Generate authentication challenge
            challenge = os.urandom(32).hex()
            
            # Send challenge to peer
            self.connected_peers[peer_id].send(json.dumps({
                'type': 'auth_challenge',
                'challenge': challenge
            }).encode('utf-8'))
            
            # Wait for response
            response = self.connected_peers[peer_id].recv(1024).decode('utf-8')
            response_data = json.loads(response)
            
            if response_data.get('type') != 'auth_response':
                return False
            
            # Verify response
            # In a real implementation, this would involve proper cryptographic verification
            return response_data.get('challenge') == challenge
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False

    def subscribe_to_topic(self, topic: str):
        """Subscribe to a topic
        
        Args:
            topic: Topic name to subscribe to
        """
        # Add topic to local subscriptions
        self.subscriptions.add(topic)
        logger.info(f"Subscribed to topic: {topic}")
        
        # Notify other peers about subscription
        for peer_id in self.connected_peers:
            self.send_message(peer_id, json.dumps({
                'topic': topic,
                'action': 'subscribe'
            }), message_type='topic_subscription')

    def unsubscribe_from_topic(self, topic: str):
        """Unsubscribe from a topic
        
        Args:
            topic: Topic name to unsubscribe from
        """
        # Remove topic from local subscriptions if exists
        if topic in self.subscriptions:
            self.subscriptions.remove(topic)
            logger.info(f"Unsubscribed from topic: {topic}")
            
            # Notify other peers about unsubscription
            for peer_id in self.connected_peers:
                self.send_message(peer_id, json.dumps({
                    'topic': topic,
                    'action': 'unsubscribe'
                }), message_type='topic_subscription')

    def publish_to_topic(self, topic: str, message: str):
        """Publish message to topic
        
        Args:
            topic: Topic name to publish to
            message: Message content to publish
        """
        content_data = json.dumps({
            'topic': topic,
            'content': message
        })
        
        # Send to peers who have subscribed to this topic
        for peer_id in self.connected_peers:
            # Check if node has subscribed to this topic
            if (peer_id in self.peer_subscriptions and 
                topic in self.peer_subscriptions[peer_id]):
                self.send_message(peer_id, content_data, message_type='topic_message')
        
        # If self has subscribed to this topic, process message locally
        if topic in self.subscriptions and hasattr(self, 'message_callback'):
            self.message_callback(self.peer_id, 'topic', f"[Topic: {topic}] {message} (local)")

    def get_available_topics(self) -> Set[str]:
        """Get all available topics in the current network
        
        Returns:
            Set[str]: Set of all topics
        """
        all_topics = set(self.subscriptions)  # First include self-subscribed topics
        
        # Add topics subscribed by all peers
        for subscriptions in self.peer_subscriptions.values():
            all_topics.update(subscriptions)
        
        return all_topics

    def get_topic_subscribers(self, topic: str) -> Set[str]:
        """Get list of peers subscribed to the specified topic
        
        Args:
            topic: Topic name
            
        Returns:
            Set[str]: Set of peer IDs subscribed to this topic
        """
        subscribers = set()
        
        # Check if self is subscribed
        if topic in self.subscriptions:
            subscribers.add(self.peer_id)
        
        # Check other peers
        for peer_id, subscriptions in self.peer_subscriptions.items():
            if topic in subscriptions:
                subscribers.add(peer_id)
        
        return subscribers

    def get_peer_subscriptions(self, peer_id: str) -> Set[str]:
        """Get all subscriptions of the specified peer
        
        Args:
            peer_id: Peer ID
            
        Returns:
            Set[str]: Set of all topics subscribed by this peer
        """
        # If querying self
        if peer_id == self.peer_id:
            return set(self.subscriptions)
        
        # If querying other peers
        if peer_id in self.peer_subscriptions:
            return set(self.peer_subscriptions[peer_id])
        
        return set()  # No subscriptions or peer doesn't exist

    def send_keepalive(self):
        """Send keep-alive packet to discovery server"""
        if self.discovery_server:
            try:
                self.discovery_server.send(json.dumps({
                    'type': 'keepalive',
                    'peer_id': self.peer_id
                }).encode('utf-8'))
            except Exception as e:
                logger.error(f"Failed to send keep-alive: {e}")

    def start_keepalive(self):
        """Start keep-alive mechanism"""
        def send_periodic_keepalive():
            while True:
                self.send_keepalive()
                time.sleep(30)  # Send keep-alive every 30 seconds
            
        keepalive_thread = threading.Thread(target=send_periodic_keepalive)
        keepalive_thread.daemon = True
        keepalive_thread.start()

    def start(self):
        """Start the peer node"""
        # Start keepalive mechanism
        self.start_keepalive()
        
        # Main loop for user input (if needed)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def set_message_callback(self, callback):
        """Set message receive callback function
        
        Args:
            callback: Callback function that accepts (peer_id, msg_type, content) parameters
        """
        self.message_callback = callback

    def exchange_keys(self, peer_id, peer_socket):
        """Exchange encryption keys with peer node"""
        try:
            # Generate new key
            new_key = Fernet.generate_key()
            self.session_keys[peer_id] = new_key
            
            # Send key to peer
            peer_socket.send(json.dumps({
                'type': 'key_exchange',
                'key': new_key.decode(),
                'sender_id': self.peer_id
            }).encode('utf-8'))
            
            # Receive peer's key
            response = peer_socket.recv(1024).decode('utf-8')
            response_data = json.loads(response)
            
            if response_data.get('type') == 'key_exchange':
                # Use key sent by peer
                self.session_keys[peer_id] = response_data.get('key').encode()
                logger.info(f"Completed key exchange with node {peer_id}")
        except Exception as e:
            logger.error(f"Key exchange failed: {e}")

    def perform_dh_key_exchange(self, peer_id, peer_socket):
        """Perform Diffie-Hellman key exchange"""
        try:
            # Generate parameters
            parameters = dh.generate_parameters(generator=2, key_size=2048)
            
            # Generate our private key
            private_key = parameters.generate_private_key()
            public_key = private_key.public_key()
            
            # Serialize parameters and public key
            param_bytes = parameters.parameter_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.ParameterFormat.PKCS3
            )
            public_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            
            # Send to peer
            peer_socket.send(json.dumps({
                'type': 'dh_exchange',
                'params': param_bytes.decode(),
                'public_key': public_bytes.decode(),
                'sender_id': self.peer_id
            }).encode('utf-8'))
            
            # Receive peer's public key
            response = peer_socket.recv(4096).decode('utf-8')
            response_data = json.loads(response)
            
            if response_data.get('type') == 'dh_exchange':
                # Parse peer's public key
                peer_public_key = serialization.load_pem_public_key(
                    response_data.get('public_key').encode()
                )
                
                # Calculate shared key
                shared_key = private_key.exchange(peer_public_key)
                
                # Derive Fernet key from shared key
                derived_key = HKDF(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=None,
                    info=b'handshake data'
                ).derive(shared_key)
                
                # Convert derived key to Fernet key format
                fernet_key = base64.urlsafe_b64encode(derived_key)
                self.session_keys[peer_id] = fernet_key
                
                logger.info(f"Completed Diffie-Hellman key exchange with node {peer_id}")
        except Exception as e:
            logger.error(f"Diffie-Hellman key exchange failed: {e}")

    def is_user_muted(self, peer_id: str) -> bool:
        """Check if user is muted
        
        Args:
            peer_id: Peer ID to check
            
        Returns:
            bool: True if user is muted and mute time hasn't expired, False otherwise
        """
        # Check if user is in the muted list
        if peer_id not in self.muted_users:
            return False
        
        # Get mute expiration time
        mute_until = self.muted_users[peer_id]
        
        # Check if mute time has expired
        if time.time() > mute_until:
            # If expired, remove from muted list
            del self.muted_users[peer_id]
            return False
        
        # User is still muted
        return True
    
    def get_mute_remaining_time(self, peer_id: str) -> int:
        """Get remaining mute time for a user (in seconds)
        
        Args:
            peer_id: Peer ID to check
            
        Returns:
            int: Remaining mute time in seconds, 0 if user is not muted
        """
        if not self.is_user_muted(peer_id):
            return 0
        
        # Calculate remaining time
        remaining = int(self.muted_users[peer_id] - time.time())
        return max(0, remaining)  # Ensure not returning negative values
    
    def get_muted_users(self) -> Dict[str, int]:
        """Get all muted users and their remaining mute time
        
        Returns:
            Dict[str, int]: Dictionary containing muted peer IDs and remaining mute time (seconds)
        """
        # Clean up expired mute settings
        current_time = time.time()
        expired = [peer_id for peer_id, mute_until in self.muted_users.items() 
                   if current_time > mute_until]
        
        for peer_id in expired:
            del self.muted_users[peer_id]
        
        # Return dictionary of remaining times
        return {peer_id: int(mute_until - current_time) 
                for peer_id, mute_until in self.muted_users.items()}

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    peer = P2PPeer(port=port)
    peer.start() 
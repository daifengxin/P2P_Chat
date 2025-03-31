import socket
import threading
import json
import hashlib
import time
from typing import Dict, Tuple
import argparse

class P2PChatServer:
    def __init__(self, host: str = 'localhost', port: int = 5000):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((host, port))
        self.server_socket.listen(5)
        self.peers = {}  # peer_id -> (socket, address, last_seen)
        print(f"Discovery Server listening on port {port}")
        
    def start(self):
        """Start discovery server"""
        while True:
            try:
                client_socket, address = self.server_socket.accept()
                peer_id = hashlib.sha256(f"{address[0]}:{time.time()}".encode()).hexdigest()[:12]
                print(f"New peer connected: {peer_id} from {address}")
                
                # Initialize port as None, wait for peer to send actual listening port
                self.peers[peer_id] = (client_socket, address[0], None)
                
                peer_thread = threading.Thread(target=self.handle_peer, args=(peer_id,))
                peer_thread.daemon = True
                peer_thread.start()
                
            except Exception as e:
                print(f"Error accepting connection: {e}")
                
    def cleanup_inactive_peers(self):
        while True:
            current_time = time.time()
            inactive_peers = [
                peer_id for peer_id, (_, _, last_alive) in self.peers.items()
                if current_time - last_alive > 30
            ]
            for peer_id in inactive_peers:
                self.remove_peer(peer_id)
            time.sleep(10)
            
    def handle_peer(self, peer_id: str):
        """Handle peer connection and messages"""
        peer_socket = self.peers[peer_id][0]
        
        try:
            while True:
                try:
                    message = peer_socket.recv(1024).decode('utf-8')
                    if not message:
                        break
                    
                    msg_data = json.loads(message)
                    print(f"Received message from {peer_id}: {msg_data}")
                    
                    if msg_data.get('type') == 'discovery':
                        # Use peer's listening port instead of connection port
                        listen_port = msg_data.get('listen_port')
                        self.peers[peer_id] = (peer_socket, self.peers[peer_id][1], listen_port)
                        
                        active_peers = {
                            pid: {
                                'addr': addr,
                                'port': port
                            } for pid, (_, addr, port) in self.peers.items()
                            if pid != peer_id
                        }
                        print(f"Sending peers to {peer_id}: {active_peers}")
                        response = {
                            'type': 'discovery_response',
                            'peers': active_peers
                        }
                        peer_socket.send(json.dumps(response).encode('utf-8'))
                    
                    # Handle specific peer lookup request - used for reconnection after unblocking
                    elif msg_data.get('type') == 'peer_lookup':
                        lookup_peer_id = msg_data.get('peer_id')
                        if lookup_peer_id in self.peers:
                            # Get connection information for this peer
                            _, addr, port = self.peers[lookup_peer_id]
                            print(f"Peer lookup request from {peer_id} for {lookup_peer_id}")
                            
                            # Return the found peer information
                            response = {
                                'type': 'peer_lookup_response',
                                'peer_id': lookup_peer_id,
                                'peer_info': {
                                    'addr': addr,
                                    'port': port
                                }
                            }
                            peer_socket.send(json.dumps(response).encode('utf-8'))
                        else:
                            # Peer does not exist
                            response = {
                                'type': 'peer_lookup_response',
                                'peer_id': lookup_peer_id,
                                'peer_info': None,
                                'error': 'Peer not found'
                            }
                            peer_socket.send(json.dumps(response).encode('utf-8'))
                
                except json.JSONDecodeError:
                    print(f"Invalid JSON from {peer_id}")
                    continue
                except socket.error as e:
                    print(f"Socket error with {peer_id}: {e}")
                    break
                
        except Exception as e:
            print(f"Error handling peer {peer_id}: {e}")
        finally:
            self.remove_peer(peer_id)
            
    def remove_peer(self, peer_id: str):
        if peer_id in self.peers:
            try:
                self.peers[peer_id][0].close()
            except:
                pass
            del self.peers[peer_id]
            print(f"Peer {peer_id} disconnected")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='P2P Discovery Server')
    parser.add_argument('--host', default='localhost', help='Server host address')
    parser.add_argument('--port', type=int, default=5000, help='Server port number')
    args = parser.parse_args()
    
    server = P2PChatServer(host=args.host, port=args.port)
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nServer shutting down...")
        for peer_id in list(server.peers.keys()):
            server.remove_peer(peer_id)
        server.server_socket.close()

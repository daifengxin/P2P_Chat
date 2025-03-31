import argparse
from peer import P2PPeer

def format_message(sender, content, is_system=False):
    """Format message display"""
    prefix = "[System]" if is_system else "[Message]"
    return f"\n{prefix} {sender}: {content}"

# Modify callback handler in peer class
def message_callback(peer_id, msg_type, content):
    """Message callback function"""
    if msg_type not in ['peer_info', 'heartbeat']:
        print(format_message(peer_id, content))
        print("[Input] > ", end='', flush=True)

def main():
    parser = argparse.ArgumentParser(description='P2P Chat Client')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--discovery-host', default='localhost')
    parser.add_argument('--discovery-port', type=int, default=5000)
    args = parser.parse_args()
    
    peer = P2PPeer(port=args.port)
    peer.connect_to_discovery_server(args.discovery_host, args.discovery_port)
    
    peer.set_message_callback(message_callback)
    
    print(f"P2P Chat started on port {peer.port}")
    print("Commands:")
    print("  peers - Show connected nodes")
    print("  <peer_id> <message> - Send message to a node")
    print("  block <peer_id> - Block a node")
    print("  unblock <peer_id> - Unblock a node")
    print("  mute <peer_id> <seconds> - Temporarily mute a node")
    print("  unmute <peer_id> - Unmute a node")
    print("  subscribe <topic> - Subscribe to a topic")
    print("  unsubscribe <topic> - Unsubscribe from a topic")
    print("  publish <topic> <message> - Publish a message to a topic")
    print("  muted - Show all muted users")
    print("  quit - Exit")
    
    try:
        while True:
            user_input = input("\n[Input] > ")
            if user_input.lower() == 'quit':
                break
                
            try:
                cmd_parts = user_input.split()
                if not cmd_parts:
                    continue
                    
                cmd = cmd_parts[0].lower()
                if cmd == 'peers':
                    peers = peer.get_connected_peers()
                    if peers:
                        print("Connected nodes:", peers)
                    else:
                        print("No connected nodes")
                elif cmd == 'block' and len(cmd_parts) == 2:
                    peer.block_user(cmd_parts[1])
                    print(f"Blocked user {cmd_parts[1]}")
                elif cmd == 'unblock' and len(cmd_parts) == 2:
                    peer.unblock_user(cmd_parts[1])
                    print(f"Unblocked user {cmd_parts[1]}")
                elif cmd == 'mute' and len(cmd_parts) == 3:
                    peer.mute_user(cmd_parts[1], int(cmd_parts[2]))
                    print(f"Muted user {cmd_parts[1]} for {cmd_parts[2]} seconds")
                elif cmd == 'unmute' and len(cmd_parts) == 2:
                    peer.unmute_user(cmd_parts[1])
                    print(f"Unmuted user {cmd_parts[1]}")
                elif cmd == 'subscribe' and len(cmd_parts) == 2:
                    topic = cmd_parts[1]
                    peer.subscribe_to_topic(topic)
                    print(f"Subscribed to topic: {topic}")
                elif cmd == 'unsubscribe' and len(cmd_parts) == 2:
                    topic = cmd_parts[1]
                    peer.unsubscribe_from_topic(topic)
                    print(f"Unsubscribed from topic: {topic}")
                elif cmd == 'publish' and len(cmd_parts) >= 3:
                    topic = cmd_parts[1]
                    message = ' '.join(cmd_parts[2:])
                    peer.publish_to_topic(topic, message)
                    print(f"Published message to topic: {topic}")
                elif cmd == 'muted':
                    muted_users = peer.get_muted_users()
                    if muted_users:
                        print("Muted users:")
                        for user_id, remaining_time in muted_users.items():
                            print(f"  {user_id}: {remaining_time} seconds remaining")
                    else:
                        print("No muted users")
                else:
                    peer_id, message = user_input.split(' ', 1)
                    peer.send_message(peer_id, message)
                    
            except ValueError:
                print("Invalid command format")
                print("Available commands: peers, block, unblock, mute, unmute, subscribe, unsubscribe, publish, muted or <peer_id> <message>")
                
    except KeyboardInterrupt:
        pass
    finally:
        peer.close()

if __name__ == '__main__':
    main() 
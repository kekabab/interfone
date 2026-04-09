import socket
import secrets

def test_sip():
    target = 'beraaa.sip.signalwire.com'
    port = 5060
    
    try:
        ip = socket.gethostbyname(target)
        print(f"Resolvendo {target} -> {ip}")
    except socket.gaierror:
        print(f"Não foi possível resolver o DNS de {target}")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0)
    
    call_id = secrets.token_hex(16)
    tag = secrets.token_hex(8)
    branch = "z9hG4bK" + secrets.token_hex(8)
    
    req = f"REGISTER sip:{target} SIP/2.0\r\n"
    req += f"Via: SIP/2.0/UDP 192.168.1.100:5060;branch={branch}\r\n"
    req += f"Max-Forwards: 70\r\n"
    req += f"To: <sip:mauricio@{target}>\r\n"
    req += f"From: <sip:mauricio@{target}>;tag={tag}\r\n"
    req += f"Call-ID: {call_id}\r\n"
    req += f"CSeq: 1 REGISTER\r\n"
    req += f"Contact: <sip:mauricio@192.168.1.100:5060>\r\n"
    req += f"Expires: 3600\r\n"
    req += f"Content-Length: 0\r\n\r\n"
    
    sock.sendto(req.encode(), (ip, port))
    try:
        data, addr = sock.recvfrom(4096)
        print("Response:\n" + data.decode('utf-8'))
    except socket.timeout:
        print(f"Timeout! No response from {ip} on UDP 5060. Possivelmente só aceita TLS/TCP ou a porta está bloqueada.")

test_sip()

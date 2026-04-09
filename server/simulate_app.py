import socketio
import time

sio = socketio.Client()

@sio.event
def connect():
    print('Conectado ao Render Socket.IO!')
    print('Enviando comando descendo...')
    sio.emit('quick_response', {'response': 'descendo'})
    time.sleep(2)
    sio.disconnect()

@sio.event
def connect_error(data):
    print('The connection failed!')

@sio.event
def disconnect():
    print('Desconectado!')

if __name__ == '__main__':
    sio.connect('wss://interfone.onrender.com')
    sio.wait()

# test_device_connection.py
from zk import ZK

DEVICE_IP = "192.168.1.100"  # your device's actual IP
PORT = 4370
COMM_KEY = 0

zk = ZK(DEVICE_IP, port=PORT, timeout=10, password=COMM_KEY, ommit_ping=True)
conn = zk.connect()
print("Connected!")
print("Device name:", conn.get_device_name())
print("Firmware:", conn.get_firmware_version())
print("Serial:", conn.get_serialnumber())
print("Users enrolled:", len(conn.get_users()))
print("Attendance records buffered:", len(conn.get_attendance()))
conn.disconnect()

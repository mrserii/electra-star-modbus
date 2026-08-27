#!/usr/bin/env python3
"""
שליטה מקומית במזגן אלקטרה Star (Mini-Central) דרך Modbus RTU.
עובד מול מתאם USB-RS485 (A/Bn = אדום+לבן, GND = שחור).

תלות: pyserial  (pip install pyserial)

דוגמאות:
  python electra_ac.py read                       # קרא מצב + טלמטריה
  python electra_ac.py off                         # כיבוי
  python electra_ac.py on --mode cool --fan low --temp 24
  python electra_ac.py set --temp 22               # שנה רק טמפ' (מצב/מאוורר נשמרים אם ידועים)
  python electra_ac.py --port /dev/ttyUSB1 read    # פורט מפורש (שרת HA)

הערות:
  * Slave = 1, 9600 8N1. דורש DIP J12 = Modbus.
  * הכתיבה ל-0x3300/1/2 עובדת; הקריאה מ-0x33xx מחזירה 0 (תיבת-פקודות).
    המצב האמיתי ב-RAM window: reg50 (hi=מצב, lo=סטפוינט), reg47-10 = אוויר חוזר.
  * הפאנל עלול "להשתלט" — הרץ שוב/מחזורית כדי להחזיק (סכמת Continuous).
"""
import sys, time, glob, argparse

MODE = {"off":0,"stby":0,"cool":1,"heat":2,"auto":3,"dry":4,"fan":5}
FAN  = {"low":0,"med":1,"high":2,"auto":3,"turbo":4,"vlow":5}
MODE_HE = {0:"כבוי",1:"קירור",2:"חימום",3:"אוטו",4:"יבש",5:"מאוורר"}
FAN_HE  = {0:"נמוך",1:"בינוני",2:"גבוה",3:"אוטו",4:"טורבו",5:"נמוך-מאוד"}

def crc(b):
    c = 0xFFFF
    for x in b:
        c ^= x
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return bytes([c & 0xFF, c >> 8])

def frame(payload):
    p = bytes(payload)
    return p + crc(p)

def find_port(explicit=None):
    if explicit:
        return explicit
    for pat in ("/dev/cu.usbserial*", "/dev/ttyUSB*", "/dev/serial/by-id/*FTDI*"):
        m = glob.glob(pat)
        if m:
            return m[0]
    sys.exit("לא נמצא מתאם סריאלי (חבר USB-RS485).")

def open_port(port):
    import serial
    return serial.Serial(port, 9600, bytesize=8, parity=serial.PARITY_NONE,
                         stopbits=1, timeout=0.5)

def txn(s, fr, wait=0.35, rl=80):
    s.reset_input_buffer(); s.write(fr); s.flush(); time.sleep(wait)
    return s.read(rl)

def parse_resp(d, sl=1, fc=3):
    for i in range(len(d) - 4):
        if d[i] == sl and d[i+1] == fc:
            for L in range(5, min(80, len(d) - i) + 1):
                f = d[i:i+L]
                if crc(f[:-2]) == f[-2:]:
                    return f
    return None

def read_reg(s, addr, n=1, slave=1):
    d = txn(s, frame([slave, 3, addr >> 8, addr & 0xFF, 0, n]))
    f = parse_resp(d, slave)
    if f and f[1] == 3 and f[2] >= 2 * n:
        return [(f[3 + 2*k] << 8) | f[4 + 2*k] for k in range(n)]
    return None

def write_state(s, mode, fan, temp):
    # fc16 ל-0x3300: מצב, מאוורר, טמפ' בבת אחת (retransmit ×3)
    payload = [1, 0x10, 0x33, 0x00, 0x00, 0x03, 0x06,
               0x00, mode & 0xFF, 0x00, fan & 0xFF, 0x00, temp & 0xFF]
    fr = frame(payload)
    for _ in range(3):
        s.write(fr); s.flush(); time.sleep(0.3)
    return fr

def cmd_read(s):
    # מצב אמיתי מ-slave 0xA0 (ממשק BMS רשמי); heartbeat מ-slave 1
    A0 = 0xA0
    mode = read_reg(s, 0x3300, slave=A0)
    fan  = read_reg(s, 0x3301, slave=A0)
    spt  = read_reg(s, 0x3302, slave=A0)
    room = read_reg(s, 0x3303, slave=A0)
    hb   = read_reg(s, 66, slave=1)
    print("── מצב מזגן אלקטרה (slave 0xA0) ──")
    if mode is not None: print(f"  מצב:        {MODE_HE.get(mode[0], mode[0])}")
    if fan  is not None: print(f"  מאוורר:     {FAN_HE.get(fan[0], fan[0])}")
    if spt  is not None: print(f"  סטפוינט:    {spt[0]}°C")
    if room is not None: print(f"  טמפ' חדר:   {room[0]}°C")
    print(f"  heartbeat:  {hb[0] if hb else '—'}  (slave 1; משתנה=תקשורת חיה)")

def main():
    ap = argparse.ArgumentParser(description="שליטה במזגן אלקטרה Star דרך Modbus")
    ap.add_argument("action", choices=["read","on","off","set"])
    ap.add_argument("--port")
    ap.add_argument("--mode", choices=list(MODE), default="cool")
    ap.add_argument("--fan",  choices=list(FAN),  default="low")
    ap.add_argument("--temp", type=int, default=24)
    a = ap.parse_args()

    s = open_port(find_port(a.port))
    try:
        if a.action == "read":
            cmd_read(s)
        elif a.action == "off":
            write_state(s, 0, FAN[a.fan], a.temp)
            print("📤 כיבוי נשלח.")
        elif a.action in ("on", "set"):
            m = 0 if a.action == "on" and a.mode == "off" else MODE[a.mode]
            write_state(s, m, FAN[a.fan], a.temp)
            print(f"📤 נשלח: {MODE_HE.get(m,m)} · מאוורר {FAN_HE[FAN[a.fan]]} · {a.temp}°")
    finally:
        s.close()

if __name__ == "__main__":
    main()

import requests
import time
import serial
import logging
from systemd.journal import JournalHandler
import paho.mqtt.client as mqtt # for MQTT. TO install on python3: pip3 install paho-mqtt
import threading

## CREATE GLOBALS
byte0 = 36
byte1 = 86
byte2 = 0
byte3 = 33
byte4 = 0 ##(2 byte watts as short integer xaxb)
byte5 = 0 ##(2 byte watts as short integer xaxb)
byte6 = 128
byte7 = 8 ## checksum


numberOfUnits = 2 # number of inverters in your system

maxOutput = 1600 # edit this to limit TOTAL power output in watts (not individual unit output)

buffer = -100 # how much of an import buffer in watts you would like, minus values will allow exporting (use cautiously)

serialWrite = serial.Serial('/dev/serial0', 4800, timeout=1) # define serial port on which to output RS485 data

last_call = time.time() # set global variable for use in the interrupt timer
outage_counter = 0
outage_state = False
## VIRTUAL METER FOR SOYO SOURCE BRAND POWER INVERTERS
# User can choose between JSON or MQTT connections, an example is each is provided. Simply comment out/in configuration depending on your use case.


jsonSource = "http://<IP-ADDR>/feed/get.json?<FEED ID>" #emonCMS json feed for CT clamp 1

broker_address="192.168.86.248" # MQTT broker address

# Needed for logging data when installed as a systemd service
log = logging.getLogger('__name__')
log_fmt = logging.Formatter("%(levelname)s %(message)s")
log_ch = JournalHandler()
log_ch.setFormatter(log_fmt)
log.addHandler(log_ch)
log.setLevel(logging.INFO)

############## JSON SECTION BELOW ##########################

def jsonSignal():
    r = requests.get(jsonSource)
    sourceValue = r.json()
    log.info("signal value from your source= %s", sourceValue)
    return sourceValue

############## MQTT SECTION BELOW #####################

def parse_message(client, userdata, message):
    global last_call
    last_call = time.time()
    signal = int(str(message.payload.decode("utf-8")))
    log.info("signal from source= %s", signal)
    demand = computeDemand(signal)
    log.info("calculated demand= %s", demand)
    simulatedPacket = createPacket(demand)
    log.info("packet bytes= %s", simulatedPacket)
    writeToSerial(simulatedPacket)

def on_connect(client, userdata, flags, rc):
    client.subscribe("emon/emonpi/power1")
    log.info("connected to MQTT")

def timer_fire():
    global last_call
    global outage_counter
    global outage_state
    timer_check = time.time()
    timer = timer_check - last_call
    log.info("INTERRUPT TIMER: %s", timer) 
    log.info("NUMBER OF OUTAGES: %s", outage_counter)
    if timer >= 5: #allowed time since last packet was recieved before entering critical condition.
        if outage_state == False:
            outage_state = True
            outage_counter += 1
        log.error("NETWORK CONNECTIVITY IS DOWN, ENTERING DEGRADED STATE, SENDING 0 WATTS SIGNAL TO INVERTER")
        simulatedPacket = (0, 0, 8)
        for times in range(5):
            writeToSerial(simulatedPacket)
            time.sleep(1)
        timer_check = time.time()
    elif timer <= 3: outage_state = False
    threading.Timer(1, timer_fire).start() #how often to check to see if network connectivity is down (not the exception timer)
    
def mqttSignal():
    now = time.time() #used to measure time before system enters degraded state
    client = mqtt.Client("P1", clean_session=True) #create new instance
    client.username_pw_set(username="emonpi",password="emonpimqtt2016") #auth is needed for emonpi
    client.on_connect = on_connect    
    client.on_message = parse_message #attach function to callback
    client.connect(broker_address) #connect to broker
    threading.Timer(10, timer_fire).start() #dontstart kill switch until booted for 5 seconds
    client.loop_forever()
     
################ FUNCTIONS ########################
def computeDemand(sourceValue):
    if sourceValue > maxOutput+buffer: #if demand is higher than our max and buffer
        demand = maxOutput/numberOfUnits
        return int(demand) # s
    elif sourceValue > maxOutput:
        demand = (abs(maxOutput-buffer))/numberOfUnits
        return int(demand)
    elif sourceValue >= 1: # if demand is above zero but less than max
        demand = ((abs(sourceValue-buffer)+(sourceValue-buffer))/2)/numberOfUnits # this is to avoid negative values at low demand
        return int(demand)
    elif sourceValue < 1: # if exporting lets reduce the output to zero
        demand = 0
        return int(demand) # only demand is required but value is for logs
    else:
        log.warning("Invalid source value")

########################################
def createPacket(demand):
    byte4 = int(demand/256) ## (2 byte watts as short integer xaxb)
    if byte4 < 0 or byte4 > 256:
        byte4 = 0
    byte5 = int(demand)-(byte4 * 256) ## (2 byte watts as short integer xaxb)
    if byte5 < 0 or byte5 > 256:
        byte5 = 0
    byte7 = (264 - byte4 - byte5) #checksum calculation
    if byte7 > 256:
        byte7 = 8
    return byte4, byte5, byte7

########################################
def writeToSerial(packet):
    try:
        bytes = [byte0,byte1,byte2,byte3,packet[0],packet[1],byte6,packet[2]]
        serialWrite.write(bytearray(bytes))
        log.info("complete decimal packet: %s", bytes)
        log.info("raw bytearray packet being sent to serial: %s", bytearray(bytes))
        log.info("checksum calc= %s", 264-packet[0]-packet[1])
    except ValueError:
        log.error("Error writing to serial port, check port settings are correct in /dev/ ")
    return packet


mqttSignal() #uncomment for mqtt/comment for json

#while True: # this while is not used if using mqtt
#    signal = jsonSignal()
#    demand =computeDemand(signal)
#    log.info("calculated demand based on your parameters= %s", demand)
#    simulatedPacket = createPacket(demand)
#    log.info("calculated packet bytes based on demand= %s", simulatedPacket)
#    writeToSerial(simulatedPacket)
#    log.info("")
#    time.sleep(1) # run four times per second

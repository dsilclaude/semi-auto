import pyvisa as visa

VISA_ADDRESS = "TCPIP0::localhost::inst0::INSTR"

# Create a connection (session) to the instrument
resourceManager = visa.ResourceManager()
session = resourceManager.open_resource(VISA_ADDRESS)

def convertStrings(oldString):
    # Remove the quotation marks and new line char
    newString = oldString.replace('"', '')
    newString = newString.replace('\n', '')
    newString = newString.split(',')
    return newString

# Read the current measurements in Channel 1
currMeas = session.query("CALC:PAR:CAT:EXT?")
currMeas = convertStrings(currMeas)
print(f"Ch1 Measurements: {currMeas}\n")

# Read the current windows
currWindow = session.query("DISP:CAT?")
currWindow = convertStrings(currWindow)
print(f"Windows: {currWindow}\n")

# Read trace numbers in window 1, returns string "EMPTY" if no traces
if (currWindow == ['EMPTY']):
    currTrace = ['EMPTY']
else:
    currTrace = session.query("DISP:WIND1:CAT?")
    currTrace = convertStrings(currTrace)
print(f"Traces in Window1: {currTrace}\n")
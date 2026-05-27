import pyvisa

rm = pyvisa.ResourceManager()
inst = rm.open_resource('TCPIP0::localhost::hislip0::INSTR')
inst.timeout = 20000

print(inst.query("*IDN?"))
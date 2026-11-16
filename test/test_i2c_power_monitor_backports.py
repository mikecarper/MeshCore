"""Execute the production INA219/INA228 init, telemetry and voltage callbacks."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class PowerMonitorBackportTests(unittest.TestCase):
    def test_alternate_address_failure_and_ina228_units(self):
        source = (ROOT / 'src/helpers/sensors/EnvironmentSensorManager.cpp').read_text()
        methods = '\n'.join(body(source, signature) for signature in (
            'static uint8_t init_ina219(', 'static void query_ina219(',
            'static bool query_ina219_voltage(', 'static uint8_t init_ina228(',
            'static void query_ina228(', 'static bool query_ina228_voltage('))
        code = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <limits>
using std::isfinite;
struct TwoWire {};
#define TELEM_INA219_ADDRESS 0x40
#define TELEM_INA228_MAX_AMP 10.0
#define TELEM_INA228_SHUNT_VALUE 0.015
struct Fake219 {
  bool works=true; float volts; bool begin(TwoWire*) { return works; }
  float getBusVoltage_V(){return volts;}
  float getCurrent_mA(){return 2000;}
  float getPower_mW(){return 10000;}
  bool success(){return works;}
};
using Adafruit_INA219 = Fake219;
Fake219 INA219{true,12},INA219_ALT{true,13};
Fake219* active_ina219=&INA219;
struct Fake228 {
  bool connected=true; unsigned manufacturer=0x5449,id=0x0228;
  int calibration=0,error=0; float volts=24;
  bool begin(){return connected;}
  unsigned getManufacturer(){return manufacturer;}
  unsigned getDieID(){return id;}
  int setMaxCurrentShunt(double amps,double shunt){assert(amps==10 && shunt==0.015);return calibration;}
  int getLastError(){return error;}
  float getBusVoltage(){return volts;}
  float getAmpere(){return 2;}
  float getWatt(){return 48;}
  double getKiloWattHour(){return 1.25;}
} INA228;
struct CayenneLPP {
  double v=0,a=0,w=0,kwh=0;
  void addVoltage(uint8_t,double x){v=x;}
  void addCurrent(uint8_t,double x){a=x;}
  void addPower(uint8_t,double x){w=x;}
  void addEnergy(uint8_t,double x){kwh=x;}
};
@METHODS@
int main(){
 TwoWire wire; CayenneLPP lpp;float volts=0;
 assert(init_ina219(&wire,0x41)==1);
 query_ina219(2,0,lpp); assert(lpp.v==13 && lpp.a==2 && lpp.w==10);
 assert(query_ina219_voltage(0,volts) && volts==13);
 INA219.works=false;
 assert(init_ina219(&wire,0x40)==0 && active_ina219==&INA219_ALT);
 assert(query_ina219_voltage(0,volts) && volts==13);
 INA219.works=true;assert(init_ina219(&wire,0x40)==1);
 assert(query_ina219_voltage(0,volts) && volts==12);
 INA219.volts=std::numeric_limits<float>::quiet_NaN();assert(!query_ina219_voltage(0,volts));
 assert(init_ina228(&wire,0x45)==1);
 query_ina228(3,0,lpp); assert(lpp.v==24 && lpp.a==2 && lpp.w==48 && lpp.kwh==1.25);
 assert(query_ina228_voltage(0,volts) && volts==24);
 INA228.error=1;assert(!query_ina228_voltage(0,volts) && init_ina228(&wire,0x45)==0);
 INA228.error=0;INA228.calibration=1;assert(init_ina228(&wire,0x45)==0);
 INA228.calibration=0;INA228.id=0x226;assert(init_ina228(&wire,0x45)==0);
 INA228.id=0x228;INA228.manufacturer=0;assert(init_ina228(&wire,0x45)==0);
 INA228.manufacturer=0x5449;INA228.connected=false;assert(init_ina228(&wire,0x45)==0);
}
'''.replace('@METHODS@', methods)
        with tempfile.TemporaryDirectory() as temporary:
            cpp, binary = Path(temporary)/'test.cpp', Path(temporary)/'test'
            cpp.write_text(code)
            compiled = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                            str(cpp), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == '__main__':
    unittest.main()

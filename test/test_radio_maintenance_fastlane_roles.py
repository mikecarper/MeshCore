#!/usr/bin/env python3
"""Verify real role-loop placement protects every slow application path."""
from pathlib import Path
import unittest
from cpp_source import body
ROOT=Path(__file__).resolve().parents[1]
class RoleFastLane(unittest.TestCase):
 def test_all_main_loops_service_before_work_and_after_mesh_admission(self):
  for role in ('simple_repeater','simple_room_server','simple_sensor','companion_radio'):
   with self.subTest(role=role):
    source=(ROOT/'examples'/role/'main.cpp').read_text();source=source[source.index('\nvoid loop() {'):]
    loop=body(source,'void loop()');first=loop.index('serviceAGCMaintenanceFastLane()');last=loop.rindex('serviceAGCMaintenanceFastLane()')
    self.assertEqual(loop.count('serviceAGCMaintenanceFastLane()'),2)
    for slow in ('board.loop()', 'sensors.loop()', 'the_mesh.loop()'):
     if slow in loop:self.assertLess(first,loop.index(slow))
     else:self.assertEqual((role,slow),('simple_repeater','board.loop()'));self.assertLess(first,loop.index('serviceCommandInterfaces()'))
    self.assertGreater(last,loop.index('the_mesh.loop()'));self.assertLess(last,loop.index('sensors.loop()'))
    self.assertIn('#if MESH_WARM_RX_REFRESH',loop[:first])
 def test_role_callbacks_short_circuit_before_and_after_base_loop(self):
  for role,file,signature,base in (
   ('simple_repeater','MyMesh.cpp','void MyMesh::loop()','mesh::Mesh::loop()'),
   ('simple_room_server','MyMesh.cpp','void MyMesh::loop()','mesh::Mesh::loop()'),
   ('simple_sensor','SensorMesh.cpp','void SensorMesh::loop()','mesh::Mesh::loop()'),
   ('companion_radio','MyMesh.cpp','void MyMesh::loop()','BaseChatMesh::loop()')):
   with self.subTest(role=role):
    loop=body((ROOT/'examples'/role/file).read_text(),signature)
    self.assertLess(loop.index('serviceAGCMaintenanceFastLane()'),loop.index(base))
    self.assertGreater(loop.index('isAGCMaintenanceActive()'),loop.index(base))
    self.assertIn('#if MESH_WARM_RX_REFRESH',loop[:loop.index('serviceAGCMaintenanceFastLane()')])
if __name__=='__main__':unittest.main()

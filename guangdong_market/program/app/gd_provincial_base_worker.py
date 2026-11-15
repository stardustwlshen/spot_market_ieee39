"""Isolated fresh provincial-base factory, no native solve or historical recovery."""
from pathlib import Path
import sys,json
APP=Path(__file__).resolve().parent
sys.path[:0]=[str(APP),str(APP/'lifecycle_runtime'),str(APP/'lifecycle_runtime/guangdong_event_host_candidate_v1_20261113')]
from v6_host import installed
from gd_provincial15_model.base_initializer import initialize
if __name__=='__main__':
 if len(sys.argv)!=4:raise SystemExit('source_base da_manifest output')
 print(json.dumps(initialize(source_base=sys.argv[1],da_manifest=sys.argv[2],output=sys.argv[3],program=installed(),research=True)))

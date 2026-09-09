#!/usr/bin/env python3
"""Check documented restart configuration changes on an isolated test cluster."""
import argparse,json
from correctness import Campaign
p=argparse.ArgumentParser();p.add_argument('--pg-bin',required=True);p.add_argument('--workdir',required=True);p.add_argument('--initial',type=int,default=1048576);p.add_argument('--final',type=int,default=64);a=p.parse_args();a.hook_probe=False
c=Campaign(a)
try:
 c.setup();c.stop()
 with open(c.data/'postgresql.conf','a') as f:f.write(f'\npg_stat_log.max_entries={a.initial}\n')
 c.start();c.assertion('initial-capacity',c.integer('SELECT max_entries FROM pg_stat_log_info()'),a.initial)
 c.reset();c.sql(c.emit('Z8888',2));c.assertion('initial-count',c.count('Z8888'),2);c.stop()
 with open(c.data/'postgresql.conf','a') as f:f.write(f'\npg_stat_log.max_entries={a.final}\n')
 result=c.run('pg_ctl',['-D',str(c.data),'-l',str(c.root/'server.log'),'-w','-t','20','start'],check=False)
 c.running=result.returncode==0
 c.assertion('restart-success',result.returncode,0,category='restart-contract')
 if c.running:c.assertion('final-capacity',c.integer('SELECT max_entries FROM pg_stat_log_info()'),a.final)
finally:
 if c.running:c.stop()
 c.save();c.transcript.close()
raise SystemExit(int(any(r['status'] != 'pass' for r in c.rows)))

import argparse
import os
import uvicorn
from app import config

parser=argparse.ArgumentParser(); parser.add_argument('service',choices=['approval','verify']); args=parser.parse_args()
prefix='APPROVAL' if args.service=='approval' else 'VERIFY'
uvicorn.run('app.main:'+args.service+'_app',host=os.getenv(prefix+'_HOST','127.0.0.1'),port=int(os.getenv(prefix+'_PORT','15001' if args.service=='approval' else '15002')),access_log=False,proxy_headers=False,server_header=False)

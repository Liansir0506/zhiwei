"""Owned background original host entry using the reviewed public profile."""
import argparse
import socket
from pathlib import Path
from scripts.original_local_runtime import ROOT,build_profile,apply_profile,read,digest,public_profile

def loopback_sockets(port):
    """Bind both local address families; fail closed if either is occupied."""
    sockets=[]
    try:
        for family,address in ((socket.AF_INET,'127.0.0.1'),(socket.AF_INET6,'::1')):
            listener=socket.socket(family,socket.SOCK_STREAM);sockets.append(listener)
            if family==socket.AF_INET6:listener.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
            listener.bind((address,port))
        return sockets
    except BaseException:
        for listener in sockets:listener.close()
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',required=True);parser.add_argument('--profile',required=True)
    args=parser.parse_args()
    if Path(args.profile).resolve()!=ROOT/'profile.json':raise ValueError('owned public profile required')
    state=read(ROOT/'state.json');settings=build_profile()
    if args.instance!=state['instance'] or digest(ROOT/'profile.json')!=state['profile_sha256']:
        raise ValueError('owned runtime identity changed')
    if read(ROOT/'profile.json')!=public_profile(settings):raise ValueError('public runtime profile changed')
    apply_profile(settings)
    import torch
    torch.set_num_threads(4)
    from app import app
    @app.get('/local/runtime',include_in_schema=False)
    def identity():return {'instance':args.instance,'generation_enabled':settings.generation_enabled,'agent_enabled':False}
    import uvicorn
    sockets=loopback_sockets(settings.api_port)
    try:
        uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False)).run(sockets=sockets)
    finally:
        for listener in sockets:listener.close()

if __name__=='__main__':main()

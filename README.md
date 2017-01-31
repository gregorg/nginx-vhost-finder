# nginx-vhost-finder

This tool will search for the NGiNX server block config that will respond to a particular domain name, considering DNS resolution, ports and server_name resolution.

This tool can be usefull on NGiNX configs that desserves many IPs, virtual hosts, with complex server_name resolution, it does care of default server and of [NGiNX server_name resolution order](http://nginx.org/en/docs/http/server_names.html)

## Usage

```
python3 nginx-vhost-finder.py /path/to/nginx.conf virtualhost [--summary] [--debug]
```


## Installation

Clone it, then install system dependencies :
```
apt-get install python3-dnspython libpcre3-dev
```

then install Python 3 libs
```
pip3 install --user -r requirements.txt
```



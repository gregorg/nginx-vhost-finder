#!/usr/bin/env python3

import sys
import re
import argparse
import logging
import glob
import dns.resolver
import pcre


class NginxServer(object):
    SERVER_NAME             = re.compile(r'\s*server_name\s+(.+);$')
    LISTEN                  = re.compile(r'\s*listen\s+(.+);$')
    PORT                    = re.compile(r'^\d+$')
    BIND                    = re.compile(r'^[0-9:.]+$')
    def __init__(self, block):
        self.server_names = []
        self.listen_to = []
        self.block = block
        for line in block:
            resrv = self.SERVER_NAME.match(line)
            if resrv:
                names = resrv.group(1).split()
                self.server_names.extend(names)
            relisten = self.LISTEN.match(line)
            if relisten:
                listen = relisten.group(1).split()
                self.listen_to.append(listen)

    def server_name(self):
        try:
            return self.server_names[0]
        except IndexError: return ''


    def __str__(self):
        return "listening on %s"%(", ".join(["/".join(a) for a in self.listen_to]))


    def is_http(self, port):
        for l in self.listen_to:
            for part in l:
                if str(port) == part or part.endswith(":%d"%port):
                    return True
        # if no listen directive, by default it desserves HTTP:
        if len(self.listen_to) == 0:
            return True
        return False

    def is_https(self, port):
        for l in self.listen_to:
            if 'ssl' in l:
                for part in l:
                    if str(port) == part or part.endswith(":%d"%port):
                        return True
        return False

    def can_serve(self, ip, https, port):
        if (not https and self.is_http(port)) or (https and self.is_https(port)):
            for l in self.listen_to:
                if "%s:%d"%(ip, port) in l:
                    return True
                if str(port) in l:
                    return True
        return False


    def is_default_server_name(self):
        return '_' in self.server_names

    def is_default_server(self, ip, port):
        for l in self.listen_to:
            if "%s:%d"%(ip, port) in l:
                return True
            if str(port) in l and 'default_server' in l:
                return True
        return False



class NginxParser(object):
    SERVER_BLOCK_START      = re.compile(r'\s*server\s*{')
    HTTP_BLOCK_START        = re.compile(r'\s*http\s*{')
    INCLUDE                 = re.compile(r'\s*include\s*(.+);')
    COMMENT                 = re.compile(r'\s*#')

    def __init__(self, confpath):
        self.servers = []
        logging.debug("Parse all files with includes")
        self.parse_http_block(self.parse(confpath))


    def resolv(self, vhost):
        ips = []
        # DNS resolv
        answers = dns.resolver.query(vhost, 'A')
        if len(answers) == 0:
            raise "Unable to resolv %s"%ip
        for rdata in answers:
            ips.append(rdata.address)
        return ips


    def search(self, vhost, https=False, port=None):
        ips = self.resolv(vhost)
        if len(ips) > 1:
            raise "Vhost on multiple IPS not supported."

        if port is None:
            if https:
                port = 443
            else:
                port = 80
        candidates = []
        ip = ips[0]
        logging.debug("Pre-select vhost that can serve IP <%s> on %s", ip, ("HTTPS" if https else 'HTTP'))
        for srv in self.servers:
            if srv.can_serve(ip, https, port):
                candidates.append(srv)

        logging.debug("1st pass: exact names")
        for srv in candidates:
            if vhost in srv.server_names:
                return srv

        logging.debug("2nd pass: longest wildcard name starting with an asterisk")
        pass_candidates = []
        for srv in candidates:
            for srvname in srv.server_names:
                wildcardvhost = None
                if srvname.startswith("*."):
                    wildcardvhost = srvname[2:]
                elif srvname.startswith("."):
                    wildcardvhost = srvname[1:]
                if wildcardvhost:
                    if vhost.endswith(wildcardvhost):
                        pass_candidates.append((srv, srvname, srvname.count('.')))
        if pass_candidates:
            dots = 0
            selected = None
            for sp in pass_candidates:
                if sp[2] > dots:
                    dots = sp[2]
                    selected = sp[0]
            return selected

        logging.debug("3rd pass: longest wildcard name ending with an asterisk")
        pass_candidates = []
        for srv in candidates:
            for srvname in srv.server_names:
                wildcardvhost = None
                if srvname.endswith(".*"):
                    wildcardvhost = srvname[:-2]
                    if vhost.startswith(wildcardvhost):
                        pass_candidates.append((srv, srvname, srvname.count('.')))
        if pass_candidates:
            dots = 0
            selected = None
            for sp in pass_candidates:
                if sp[2] > dots:
                    dots = sp[2]
                    selected = sp[0]
            return selected

        logging.debug("4th pass: first matching regular expression (in order of appearance in a configuration file)")
        pass_candidates = []
        for srv in candidates:
            for srvname in srv.server_names:
                wildcardvhost = None
                if srvname.startswith('~'):
                    wildcardvhost = srvname[1:]
                    try:
                        revhost = pcre.search(wildcardvhost, vhost)
                        if revhost:
                            return srv
                    except:
                        logging.debug("FAILED to compile PCRE '%s'", wildcardvhost)

        logging.debug("5th pass: fallback to default vhost")
        for srv in candidates:
            if srv.is_default_server_name() or srv.is_default_server(ip, port):
                return srv

        return None


    def parse(self, conf):
        content = []
        with open(conf) as f:
            while True:
                line = f.readline()
                if not line:
                    break
                line = line[:-1]

                # comments
                if self.COMMENT.match(line) or line == '':
                    continue

                reinc = self.INCLUDE.match(line)
                if reinc:
                    #logging.debug("INCLUDE: %s", reinc.group(1))
                    includes = glob.glob(reinc.group(1))
                    if not includes:
                        includes = glob.glob("/etc/nginx/%s"%reinc.group(1))
                    if includes:
                        includes.sort()
                        for inc in includes:
                            content.append("# INCLUDE: %s"%inc)
                            content.extend(self.parse(inc))
                else:
                    content.append(line)
        return content


    def parse_http_block(self, content):
        logging.debug("%d lines to parse", len(content))
        in_http_block = False
        http_block = []
        block_indent = 1
        for line in content:
            if not in_http_block and self.HTTP_BLOCK_START.match(line):
                in_http_block = True
                continue
            if in_http_block:
                if '{' in line:
                    block_indent += 1
                elif '}' in line:
                    if block_indent == 0:
                        in_http_block = False
                        continue
                    else:
                        block_indent -= 1
                elif '{' in line:
                    print("NOT DETECTED: %s"%line)
                http_block.append(line)

        logging.debug("%s lines in http{}", len(http_block))
        in_server_block = False
        block_indent = 0
        server_block = []
        for line in http_block:
            if self.SERVER_BLOCK_START.match(line):
                in_server_block = True
                block_indent += 1
                server_block.append(line)
                continue
            if in_server_block:
                if '{' in line and not '}' in line:
                    block_indent += 1
                elif '}' in line and not '{' in line:
                    block_indent -= 1
                    if block_indent == 0:
                        in_server_block = False
                        # new server in server_block
                        server_block.append(line)
                        self.servers.append(NginxServer(server_block))
                        server_block = []
                        continue
                server_block.append(line)


if __name__ == '__main__':
    logging.root.setLevel(logging.DEBUG)
    parser = argparse.ArgumentParser(description='Parse NGiNX config and search for vhost')
    parser.add_argument('configpath', help='NGiNX config path')
    parser.add_argument('virtualhost', help='VirtualHost name')
    parser.add_argument('--https', help='https', action='store_true')
    parser.add_argument('--debug', help='enable debug', action='store_true')
    parser.add_argument('--summary', help='Summary', action='store_true')
    parser.add_argument('--port', help='Non-standard port', action='store', type=int)
    args = parser.parse_args()
    port = 80
    if args.https and not args.port:
        port = 443
    if args.port:
        port = args.port

    if args.debug:
        logging.root.setLevel(logging.DEBUG)
    else:
        logging.root.setLevel(logging.INFO)

    np = NginxParser(args.configpath)
    found = np.search(args.virtualhost, args.https, port)
    if found:
        if args.summary:
            print(found)
            for sn in found.server_names:
                print("server_name: %s"%sn)
        else:
            print(found.server_name())
    else:
        print(">>> NOT FOUND! no default server for %s:%d ??"%(np.resolv(args.virtualhost)[0], port))
        sys.exit(2)


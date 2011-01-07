import sys, os, pty
from AppKit import *
import my, models

NET_ALL=0
NET_AUTO=1
NET_MANUAL=2

def sshuttle_args(host, auto_nets, auto_hosts, nets):
    argv = [my.bundle_path('sshuttle/sshuttle', ''), '-v', '-r', host]
    assert(argv[0])
    if auto_nets:
        argv.append('--auto-nets')
    if auto_hosts:
        argv.append('--auto-hosts')
    argv += nets
    return argv


class _Callback(NSObject):
    def initWithFunc_(self, func):
        self = super(_Callback, self).init()
        self.func = func
        return self
    def func_(self, obj):
        return self.func(obj)


class Callback:
    def __init__(self, func):
        self.obj = _Callback.alloc().initWithFunc_(func)
        self.sel = self.obj.func_


class Runner:
    def __init__(self, argv, logfunc, promptfunc):
        print 'in __init__'
        self.id = argv
        self.rv = None
        self.pid = None
        self.fd = None
        self.logfunc = logfunc
        self.promptfunc = promptfunc
        self.buf = ''
        print 'will run: %r' % argv
        pid,fd = pty.fork()
        if pid == 0:
            # child
            try:
                os.execvp(argv[0], argv)
            except Exception, e:
                sys.stderr.write('failed to start: %r\n' % e)
                raise
            finally:
                os._exit(42)
        # parent
        self.pid = pid
        self.file = NSFileHandle.alloc()\
               .initWithFileDescriptor_closeOnDealloc_(fd, True)
        self.cb = Callback(self.gotdata)
        NSNotificationCenter.defaultCenter()\
            .addObserver_selector_name_object_(self.cb.obj, self.cb.sel,
                        NSFileHandleDataAvailableNotification, self.file)
        self.file.waitForDataInBackgroundAndNotify()

    def __del__(self):
        self.wait()

    def _try_wait(self, options):
        if self.rv == None and self.pid > 0:
            pid,code = os.waitpid(self.pid, options)
            if pid == self.pid:
                if os.WIFEXITED(code):
                    self.rv = os.WEXITSTATUS(code)
                else:
                    self.rv = -os.WSTOPSIG(code)
        print 'wait_result: %r' % self.rv
        return self.rv

    def wait(self):
        return self._try_wait(0)
        
    def poll(self):
        return self._try_wait(os.WNOHANG)

    def kill(self):
        assert(self.pid > 0)
        print 'killing: pid=%r rv=%r' % (self.pid, self.rv)
        if self.rv == None:
            os.kill(self.pid, 15)

    def gotdata(self, notification):
        print 'gotdata!'
        d = str(self.file.availableData())
        if d:
            self.logfunc(d)
            self.buf = self.buf + d
            self.buf = self.buf[-4096:]
            if self.buf.strip().endswith(':'):
                lastline = self.buf.rstrip().split('\n')[-1]
                resp = self.promptfunc(lastline)
                add = ' (response)\n'
                self.buf += add
                self.logfunc(add)
                self.file.writeData_(my.Data(resp + '\n'))
            self.file.waitForDataInBackgroundAndNotify()
        self.poll()
        print 'gotdata done!'


class SshuttleApp(NSObject):
    def initialize(self):
        d = my.PList('UserDefaults') 
        my.Defaults().registerDefaults_(d)


class SshuttleController(NSObject):
    # Interface builder outlets
    startAtLoginField = objc.IBOutlet()
    routingField = objc.IBOutlet()
    prefsWindow = objc.IBOutlet()
    serversController = objc.IBOutlet()
    logField = objc.IBOutlet()
    
    servers = []
    conns = {}

    def _connect(self, server):
        host = server.host()
        print 'connecting %r' % host
        self.fill_menu()
        def logfunc(msg):
            print 'log! (%d bytes)' % len(msg)
            self.logField.textStorage()\
                .appendAttributedString_(NSAttributedString.alloc()\
                                         .initWithString_(msg))
        def promptfunc(prompt):
            print 'prompt! %r' % prompt
            return 'scs'
        nets_mode = server.autoNets()
        if nets_mode == NET_MANUAL:
            manual_nets = ["%s/%d" % (i.subnet(), i.width())
                           for i in server.nets()]
        elif nets_mode == NET_ALL:
            manual_nets = ['0/0']
        else:
            manual_nets = []
        conn = Runner(sshuttle_args(host,
                                    auto_nets = nets_mode == NET_AUTO,
                                    auto_hosts = server.autoHosts(),
                                    nets = manual_nets),
                      logfunc=logfunc, promptfunc=promptfunc)
        self.conns[host] = conn

    def _disconnect(self, server):
        host = server.host()
        print 'disconnecting %r' % host
        self.fill_menu()
        conn = self.conns.get(host)
        if conn:
            conn.kill()
    
    @objc.IBAction
    def cmd_connect(self, sender):
        server = sender.representedObject()
        server.setConnected_(True)

    @objc.IBAction
    def cmd_disconnect(self, sender):
        server = sender.representedObject()
        server.setConnected_(False)

    @objc.IBAction
    def cmd_show(self, sender):
        self.prefsWindow.makeKeyAndOrderFront_(self)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.IBAction
    def cmd_quit(self, sender):
        NSApp.performSelector_withObject_afterDelay_(NSApp.terminate_,
                                                     None, 0.0)

    def fill_menu(self):
        menu = self.menu
        menu.removeAllItems()

        def additem(name, func, obj):
            it = menu.addItemWithTitle_action_keyEquivalent_(name, None, "")
            it.setRepresentedObject_(obj)
            it.setTarget_(self)
            it.setAction_(func)

        any_conn = False
        err = None
        if len(self.servers):
            for i in self.servers:
                host = i.host()
                if not host:
                    additem('Connect Untitled', None, i)
                elif i.connected():
                    any_conn = i
                    additem('Disconnect %s' % host, self.cmd_disconnect, i)
                else:
                    additem('Connect %s' % host, self.cmd_connect, i)
        else:
            additem('No servers defined yet', None, None)

        menu.addItem_(NSMenuItem.separatorItem())
        additem('Preferences...', self.cmd_show, None)
        additem('Quit Sshuttle VPN', self.cmd_quit, None)

        if err:
            self.statusitem.setImage_(self.img_err)
            self.statusitem.setTitle_('Error!')
        elif any_conn:
            self.statusitem.setImage_(self.img_running)
            self.statusitem.setTitle_('')
        else:
            self.statusitem.setImage_(self.img_idle)
            self.statusitem.setTitle_('')

    def load_servers(self):
        l = my.Defaults().arrayForKey_('servers') or []
        sl = []
        for s in l:
            host = s.get('host', None)
            if not host: continue
            
            nets = s.get('nets', [])
            nl = []
            for n in nets:
                subnet = n[0]
                width = n[1]
                net = models.SshuttleNet.alloc().init()
                net.setSubnet_(subnet)
                net.setWidth_(width)
                nl.append(net)
            
            autoNets = s.get('autoNets', 1)
            autoHosts = s.get('autoHosts', 1)
            srv = models.SshuttleServer.alloc().init()
            srv.setHost_(host)
            srv.setAutoNets_(autoNets)
            srv.setAutoHosts_(autoHosts)
            srv.setNets_(nl)
            sl.append(srv)
        self.serversController.addObjects_(sl)
        self.serversController.setSelectionIndex_(0)

    def save_servers(self):
        l = []
        for s in self.servers:
            host = s.host()
            if not host: continue
            nets = []
            for n in s.nets():
                subnet = n.subnet()
                if not subnet: continue
                nets.append((subnet, n.width()))
            d = dict(host=s.host(),
                     nets=nets,
                     autoNets=s.autoNets(),
                     autoHosts=s.autoHosts())
            l.append(d)
        my.Defaults().setObject_forKey_(l, 'servers')
        self.fill_menu()

    def awakeFromNib(self):
        self.routingField.removeAllItems()
        tf = self.routingField.addItemWithTitle_
        tf('Send all traffic through this server')
        tf('Determine automatically')
        tf('Custom...')

        # Hmm, even when I mark this as !enabled in the .nib, it still comes
        # through as enabled.  So let's just disable it here (since we don't
        # support this feature yet).
        self.startAtLoginField.setEnabled_(False)
        self.startAtLoginField.setState_(False)

        self.load_servers()

        # Initialize our menu item
        self.menu = NSMenu.alloc().initWithTitle_('Sshuttle')
        bar = NSStatusBar.systemStatusBar()
        statusitem = bar.statusItemWithLength_(NSVariableStatusItemLength)
        self.statusitem = statusitem
        self.img_idle = my.Image('chicken-tiny-bw', 'png')
        self.img_running = my.Image('chicken-tiny', 'png')
        self.img_err = my.Image('chicken-tiny-err', 'png')
        statusitem.setImage_(self.img_idle)
        statusitem.setHighlightMode_(True)
        statusitem.setMenu_(self.menu)
        self.fill_menu()
        
        models.configchange_callback = my.DelayedCallback(self.save_servers)
        
        def sc(server):
            if server.connected():
                self._connect(server)
            else:
                self._disconnect(server)
        models.setconnect_callback = sc


# Note: NSApplicationMain calls sys.exit(), so this never returns.
NSApplicationMain(sys.argv)

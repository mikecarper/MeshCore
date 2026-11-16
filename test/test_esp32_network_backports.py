"""Compile the production lwIP shims with thread, timeout and binding fakes."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

TYPES = r'''
#pragma once
#include <cstddef>
#include <cstdint>
using err_t=int;using u16_t=uint16_t;
constexpr int ERR_MEM=-1,LWIP_CORE_LOCK_QUERY_HOLDER=1,SOF_REUSEADDR=4;
#define SO_REUSE 1
struct ip_addr_t{int value;};
using dns_found_callback=void(*)(const char*,const ip_addr_t*,void*);
struct tcp_pcb{unsigned options=0;};
struct tcpip_api_call_data{};
inline void ip_set_option(tcp_pcb* pcb,unsigned value){pcb->options|=value;}
extern bool on_tcpip,has_semaphore;
inline bool sys_thread_tcpip(int){return on_tcpip;}
inline void* sys_thread_sem_get(){return has_semaphore?(void*)1:nullptr;}
extern int marshalled;
inline err_t tcpip_api_call(err_t(*callback)(tcpip_api_call_data*),tcpip_api_call_data* data){
 ++marshalled;on_tcpip=true;int result=callback(data);on_tcpip=false;return result;
}
'''

HARNESS = r'''
#include "types.h"
#include <cassert>
#include <cstring>
bool on_tcpip=false,has_semaphore=true;
int marshalled=0,lookups=0,binds=0;
extern "C" err_t __wrap_dns_gethostbyname(const char*,ip_addr_t*,dns_found_callback,void*);
extern "C" err_t __wrap_tcp_bind(tcp_pcb*,const ip_addr_t*,u16_t);
static void found(const char*,const ip_addr_t*,void*){}
static ip_addr_t destination{42};
extern "C" err_t __real_dns_gethostbyname(const char* name,ip_addr_t* output,
                                         dns_found_callback callback,void* argument){
 assert(!strcmp(name,"example.test")&&output==&destination&&callback==found&&argument==(void*)7);
#if !LWIP_TCPIP_CORE_LOCKING
 assert(on_tcpip);
#endif
 ++lookups;return -5;
}
extern "C" err_t __real_tcp_bind(tcp_pcb*,const ip_addr_t* address,u16_t){
 assert(address==&destination);++binds;return -9;
}
int main(){
 auto lookup=[](){return __wrap_dns_gethostbyname("example.test",&destination,found,(void*)7);};
 assert(lookup()==-5&&lookups==1);
#if !LWIP_TCPIP_CORE_LOCKING
 assert(marshalled==1);
 on_tcpip=true;assert(lookup()==-5&&lookups==2&&marshalled==1);on_tcpip=false;
 has_semaphore=false;assert(lookup()==ERR_MEM&&lookups==2&&marshalled==1);
#else
 assert(marshalled==0);has_semaphore=false;assert(lookup()==-5&&lookups==2);
#endif
 tcp_pcb web,other;
 assert(__wrap_tcp_bind(&web,&destination,80)==-9&&(web.options&SOF_REUSEADDR));
 assert(__wrap_tcp_bind(&other,&destination,5000)==-9&&other.options==0);
 assert(__wrap_tcp_bind(nullptr,&destination,80)==-9&&binds==3);
}
'''


class Esp32NetworkBackports(unittest.TestCase):
    def test_dns_dispatch_and_http_rebind(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            (work/'types.h').write_text(TYPES)
            for header in ['opt','dns','err','sys','ip','tcp','priv/tcpip_priv']:
                path=work/('lwip/'+header+'.h');path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('#pragma once\n#include <types.h>\n')
            (work/'test.cpp').write_text(HARNESS)
            for core_locking in (0,1):
                with self.subTest(core_locking=core_locking):
                    compiled=subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',
                        '-DESP_PLATFORM=1','-DLWIP_TCPIP_CORE_LOCKING='+str(core_locking),
                        '-I'+str(work),str(work/'test.cpp'),
                        str(ROOT/'src/helpers/ESP32DnsThreadFix.cpp'),
                        str(ROOT/'src/helpers/ESP32TcpBindReuse.cpp'),'-o',str(work/'test')],
                        capture_output=True,text=True)
                    self.assertEqual(compiled.returncode,0,compiled.stderr)
                    ran=subprocess.run([str(work/'test')],capture_output=True,text=True)
                    self.assertEqual(ran.returncode,0,ran.stderr)


if __name__ == '__main__':
    unittest.main()

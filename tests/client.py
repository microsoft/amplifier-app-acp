import asyncio,json,sys
class Client:
    def __init__(self,process):self.process=process;self.counter=0;self.pending={};self.events=[];self.requests=[];self.reader=asyncio.create_task(self.read())
    @classmethod
    async def launch(cls,config):
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','amplifier_acp','--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        return cls(process)
    async def read(self):
        while line:=await self.process.stdout.readline():
            row=json.loads(line)
            if row.get('method'):
                self.events.append(row)
                if 'id' in row:
                    self.requests.append(row)
                    result={'outcome':{'outcome':'selected','optionId':'deny'}} if row['method']=='session/request_permission' else {'allowed':True,'budgetRevision':0}
                    if getattr(self,'response_handler',None):result=await self.response_handler(row)
                    await self.send({'jsonrpc':'2.0','id':row['id'],'result':result})
            elif row.get('id') in self.pending:self.pending[row['id']].set_result(row)
    async def send(self,row):self.process.stdin.write((json.dumps(row)+'\n').encode());await self.process.stdin.drain()
    async def request(self,method,params,timeout=60):
        self.counter+=1;identity=self.counter;future=asyncio.get_running_loop().create_future();self.pending[identity]=future
        await self.send({'jsonrpc':'2.0','id':identity,'method':method,'params':params})
        try:return await asyncio.wait_for(future,timeout)
        except TimeoutError:
            self.process.terminate();await self.process.wait()
            raise AssertionError((await self.process.stderr.read()).decode()[-10000:])
    async def close(self):
        if self.process.returncode is None:self.process.stdin.close();await asyncio.wait_for(self.process.wait(),20)
        await self.reader
        return (await self.process.stderr.read()).decode()

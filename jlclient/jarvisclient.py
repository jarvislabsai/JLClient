from .httpclient import post, get
from jlclient import jarvisclient
import time
token = None

class Instance(object):
    def __init__(self,
                 hdd: int,
                 gpu_type: str,
                 machine_id: int,
                 num_gpus: int = None,
                 num_cpus: int = None,
                 name: str = '',
                 script_id: str = '',
                 is_reserved: bool = True,
                 url: str = '',
                 status: str = '',
                 ssh_str: str = '',
                 endpoints: str = '',
                 duration: str = 'hour',
                 script_args: str = '',
                 http_ports: str = '',
                 template: str = ''
                 ):
        
        self.gpu_type = gpu_type
        self.num_gpus = num_gpus
        self.num_cpus = num_cpus
        self.hdd = hdd
        self.name = name
        self.machine_id = machine_id
        self.script_id = script_id
        self.is_reserved = is_reserved
        self.duration = duration
        self.script_args = script_args
        self.http_ports = http_ports
        self.template = template
        self.url = url
        self.endpoints = endpoints
        self.ssh_str = ssh_str
        self.status = status

    def pause(self):
        '''
        Pause the running machine.
        Returns:
            status: Returns the pause status of the machine --> success or failed.
        '''
        pause_response = post({},f'misc/pause', 
                              jarvisclient.token,
                              query_params={'machine_id':f'{self.machine_id}'})
        if pause_response['success']:
            self.status = 'Paused'
        return pause_response
    
    def destroy(self):
        '''
        Destroy the running or paused machine. 
        Returns:
            status:  Returns the destroy status of the machine --> success or failed.
        '''
        destroy_response = post({},
                                f'misc/destroy',
                                jarvisclient.token,
                                query_params={'machine_id': self.machine_id})
        if destroy_response['success']:
            self.status = 'Destroyed'
        return destroy_response
    
    def update_instance_meta(self,req,machine_details):
        self.machine_id = machine_details.get('machine_id')
        self.gpu_type = req.get('gpu_type')
        self.num_gpus = req.get('num_gpus')
        self.hdd = req.get('hdd')
        self.is_reserved = req.get('is_reserved')
        self.name = req.get('name')
        self.num_cpus = req.get('num_cpus')
        self.url = machine_details.get('url')
        self.endpoints = machine_details.get('endpoints')
        self.ssh_str = machine_details.get('ssh_str')
        self.status = machine_details.get('status')
        self.machine_id=machine_details.get('machine_id')
        self.duration=machine_details.get('frequency')
        self.template=machine_details.get('framework')

    def resume(self,
               storage: int=None,
               num_cpus: int = None,
               num_gpus :int=None,
               gpu_type: str=None,
               name: str=None,
               script_id: str=None,
               script_args: str=None,
               is_reserved: bool=None,
               duration: str=None,
               fs_id: str=None
               ):
        resume_req = {
            'machine_id': self.machine_id,
            'hdd' :  storage or self.hdd,
            'name' : name or self.name,
            'script_id' :  script_id or self.script_id,
            'script_args' : script_args or self.script_args,
            'duration' : duration or self.duration,
            'gpu_type': None,
            'num_gpus': None,
            'num_cpus': None,
            'fs_id': fs_id
        }

        if num_cpus or self.gpu_type == 'CPU' and not num_gpus:
            resume_req['num_cpus'] = num_cpus if num_cpus else self.num_cpus
        else:
            resume_req['gpu_type'] = gpu_type if gpu_type else self.gpu_type
            resume_req['num_gpus'] = num_gpus if num_gpus else self.num_gpus
            resume_req['is_reserved'] = is_reserved if is_reserved else self.is_reserved
        
        try:
            resume_resp = post(resume_req,f'templates/{self.template}/resume', jarvisclient.token)
            self.machine_id = resume_resp['machine_id']
            machine_details = Instance.get_instance_details(machine_id=self.machine_id)
            self.update_instance_meta(req=resume_req,machine_details=machine_details)
            return self
        
        except InstanceCreationException:
            return {'error_message': 'Failed to create the instance. Please reach to the team.'}

        except Exception as e:
            return {'error_message' : "Some unexpected error had occured. Please reach to the team."}

    def get_instance_details(machine_id):
        attempts = 0
        max_attempts = 5

        while attempts < max_attempts:
            machine_status_response = get('users/fetch',
                                      jarvisclient.token)
            
            matching_instances = [instance for instance in machine_status_response['instances'] 
                                if instance.get('machine_id') == machine_id]
            machine_details = matching_instances[0] if matching_instances else None
            if machine_details['status'] == 'Running':
                return machine_details
            else:
                time.sleep(10)
                attempts+= 1
        
        if attempts == max_attempts and machine_details['status'] != 'Running':
            raise InstanceCreationException

    @classmethod
    def create(cls,
               instance_type :str,
               gpu_type: str = 'RTX5000',
               template: str = 'pytorch', 
               num_gpus: int = 1,
               num_cpus: int = 1,
               storage: int = 20,
               name: str = 'Name me',
               script_id: str = None,
               image: str = None,
               script_args: str = None,
               is_reserved :bool = True,
               duration: str = 'hour',
               http_ports : str = '',
               fs_id: str = None
               ):
        req_data = {'hdd':storage,
                    'name':name,
                    'script_id':script_id,
                    'image':image,
                    'script_args':script_args,
                    'is_reserved' :is_reserved,
                    'duration':duration,
                    'http_ports' :http_ports,
                    'fs_id':fs_id}
        instance_params = {}

        if instance_type.lower() == 'gpu':
            req_data['gpu_type'] = gpu_type
            req_data['num_gpus'] = num_gpus
            instance_params['gpu_type'] = gpu_type
            instance_params['num_gpus'] = num_gpus
        elif instance_type.lower() == 'cpu':
            req_data['num_cpus'] = num_gpus
            instance_params['gpu_type'] = 'CPU'
            instance_params['num_cpus'] = num_cpus

        try:
            resp = post(req_data, f'templates/{template}/create', jarvisclient.token)
            machine_id = resp['machine_id']
            machine_details = Instance.get_instance_details(machine_id=machine_id)
            instance_params.update({
                'hdd': storage,
                'name': machine_details.get('name'),
                'url': machine_details.get('url'),
                'endpoints': machine_details.get('endpoints'),
                'ssh_str': machine_details.get('ssh_str'),
                'status': machine_details.get('status'),
                'machine_id': machine_details.get('machine_id'),
                'duration': machine_details.get('frequency'),
                'template': machine_details.get('framework'),
        })

            instance = cls(**instance_params)
            return instance
        
        except InstanceCreationException:
            return {'error_message': 'Failed to create the instance. Please reach to the team.'}

        except Exception as e:
            return {'error_message' : "Some unexpected error had occured. Please reach to the team."}

    def __str__(self):
        """Returns a formatted string with instance metadata when the object is printed."""
        metadata = [
            f"Instance Name: {self.name}",
            f"Status: {self.status}",
            f"Machine ID: {self.machine_id}",
            f"GPU Type: {self.gpu_type}",
            f"Number of GPUs: {self.num_gpus}",
            f"Number of CPUs: {self.num_cpus}",
            f"Storage (GB): {self.hdd}",
            f"Template: {self.template}",
            f"Duration: {self.duration}",
            f"SSH Command: {self.ssh_str}",
            f"URL: {self.url}",
            f"Endpoints: {self.endpoints}"
        ]
        return "\n".join(metadata)

class InstanceCreationException(Exception):
    """Exception raised when instance creation fails."""

    def __init__(self, message="Failed to create the instance. Please check it."):
        self.message = message
        super().__init__(self.message)

class User(object):
    def __init__(self) -> None:
        pass
    
    @classmethod
    def get_instances(cls)->list[Instance]:
        resp = get(f"users/fetch", 
                    jarvisclient.token)
        instances = []
        for instance in resp['instances']:
            inst = Instance(hdd=instance['hdd'],
                            gpu_type=instance['gpu_type'],
                            name=instance['instance_name'],
                            url=instance['url'],
                            endpoints=instance['endpoints'],
                            ssh_str=instance['ssh_str'],
                            status=instance['status'],
                            machine_id=instance['machine_id'],
                            # duration=instance['frequency'],
                            template=instance['framework'],
                            num_gpus=instance['num_gpus'],
                            )
            instances.append(inst)
        return instances
    
    @classmethod
    def get_instance(cls, instance_id=None) -> Instance:
        assert instance_id != None, 'pass a valid instance/machine id'
        instances = User.get_instances()
        instance = [instance for instance in instances if str(instance.__dict__['machine_id']) == str(instance_id)]
        if len(instance) == 1:
            return instance[0]
        print("Invalid machine id")
    
    @classmethod
    def get_templates(cls):
        templates = get(f"misc/frameworks", 
                    jarvisclient.token)
        return {'templates' : [template['id'] for template in templates['frameworks']]}
    
    @classmethod
    def get_balance(cls):
        return get(f"users/balance", 
                    jarvisclient.token)
    
    @classmethod
    def get_scripts(cls):
        resp = get("/scripts/",jarvisclient.token)
        return resp['script_meta']
    
class FileSystem(object):
    def list(self):
        return get(f"filesystem/list",jarvisclient.token)
    
    def create(self, fs_name, storage):
        return post({'fs_name':fs_name,'storage':storage},
                    f"filesystem/create",
                    jarvisclient.token
                    )
    
    def delete(self, fs_id):
        return post({},
                    f"filesystem/delete",
                    jarvisclient.token,
                    query_params={'fs_id':fs_id})
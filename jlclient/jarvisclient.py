from .httpclient import post, post_files
import time
import json
token = None
user_id = None


class Instance(object):
    def __init__(self, gpu_type: str,
                 num_gpus: int,
                 hdd: int,
                 framework_id: int,
                 url: str,
                 machine_id: int,
                 tboard_url: str,
                 ssh_str: str,
                 status: str = '',
                 name: str = '',
                 ):

        self.gpu_type = gpu_type
        self.num_gpus = num_gpus
        self.hdd = hdd
        self.framework_id = framework_id
        self.url = url
        self.machine_id = machine_id
        self.tboard_url = tboard_url
        self.ssh_str = ssh_str
        self.status = status
        self.name = name

    def pause(self):
        """
        Pause the running machine.

        Returns:
            status: Returns the pause status of the machine --> success or failed.
        """
        resp = post({'jwt': token,
                     'id': self.machine_id,
                     'user_id': user_id}, 'pause')
        if resp['success']:
            self.status = 'Paused'
        return resp

    def destroy(self):
        """
        Destroy the running or paused machine. 

        Returns:
            status:  Returns the destroy status of the machine --> success or failed.
        """
        return post({'jwt': token,
                     'id': self.machine_id,
                     'user_id': user_id}, 'destroy')

    def update_instance_meta(self, req):
        self.num_gpus = req['gpus']
        self.gpu_type = req['gpu_type']
        self.hdd = req['hdd']

    def resume(self, num_gpus=None, gpu_type=None, hdd=None):
        """
        Resume the paused instance, can change the number of parameters like number of GPU's,
        GPU type and size of the volume. 

        Args:
            num_gpus (int, optional):   Number of GPU's while creating instance min (1) to max (8).
                                        For CPU instance : num_gpus=1. Defaults to None.

            gpu_type (str, optional):   Range of Nvidia GPU cards like - RTX5000, RTX6000, A100, A6000. Defaults to None.

            hdd (int, optional):        Persistance storage volume size ranges from 20GB to 500GB in multiples of 10.
                                        Defaults to None.

        Returns:
            obj: Return the resume object. If failed, return error message.
        """
        req = {'jwt': token,
               'id': self.machine_id,
               'gpus': num_gpus if num_gpus else self.num_gpus,
               'gpu_type': gpu_type if gpu_type else self.gpu_type,
               'hdd': hdd if hdd else self.hdd,
               'user_id': user_id}

        resp = post(req, 'resume')

        if resp['success']:
            self.machine_id = resp['machine_id']
            self.url = resp['url']
            self.ssh_str = resp['ssh_str']
            self.tboard_url = resp['tboard_url']
            self.status = 'Running'
            self.update_instance_meta(req)
            return {'success': True}
        else:
            return {'success': True, 'error_message': resp['error_message']}

    def __str__(self):
        return str(self.__dict__)

    def __repr__(self) -> str:
        return str(self.__dict__)

    @classmethod
    def create(cls, gpu_type: str = 'RTX5000', num_gpus: int = 1, hdd: int = 20, framework_id: int = 0, name: str = 'Name me', script_id: str = None, image: str = None):
        """
        Creates a virtual machine

        Args:
            gpu_type (str):             Range of Nvidia GPU cards like - RTX5000, RTX6000, A100, A6000. Defaults to 'RTX5000'.

            num_gpus (int):             Number of GPU's while creating instance min (1) to max (8). For CPU instance : num_gpus=1
                                        Defaults to 1, max=8

            hdd (int):                  Persistance storage volume size ranges from 20GB to 500GB in multiples of 10. Defaults to hdd=20, in GB. max=500 in GB

            framework_id (int):         Optimized NGC container with various deeplearning framework like
                                        {Pytorch : 0, Fastai : 1, Tensorflow-2: 2, BYOC : 3}. Defaults to 0.

            name (str, optional):       Name the instance which is meaningful to differentiate with other instance. Defaults to Name me.

            script_id (str, optional):  Name the script to understand what kind of function it does. Defaults to None.

            image (str, optional):      Name of the docker image like pytorch/pytorch. Applies only for the BYOC mode.
                                        Please select the framework_id=3. Defaults to None.

        Returns:
            obj: instance object which contains the jupyterlab url, machine_id, ssh_str etc.,
        """
        req_data = {'jwt': token,
                    'user_id': user_id,
                    'gpuType': gpu_type,
                    'gpus': num_gpus,
                    'hdd': hdd,
                    'framework': str(framework_id),
                    'ram': f"{num_gpus*32}GB",
                    'cores': f"{num_gpus*7}",
                    'name': name,
                    'script_id': script_id,
                    'image': image
                    }

        resp = post(req_data, 'create')
        if resp.get('detail', None):
            return {'error': [o['msg'] for o in resp['detail']]}
        if resp['error_code']:
            return {'error': resp['error_message']}

        instance = cls(gpu_type, num_gpus, hdd, framework_id,
                       resp['url'], resp['machine_id'], resp['tboard_url'], resp['ssh_str'], 'Running', name)
        return instance


class User(object):

    @classmethod
    def get_instances(cls):
        resp = post({'jwt': token,
                     'user_id': user_id}, 'fetch')
        instances = []
        for o in resp:
            for key, inst_o in o.items():
                inst = Instance(gpu_type=inst_o['gpu_type'],
                                num_gpus=inst_o['num_gpus'],
                                hdd=inst_o['hdd'],
                                framework_id=inst_o['framework_id'],
                                url=inst_o['url'],
                                machine_id=inst_o['machine_id'],
                                tboard_url=inst_o['tboard'],
                                ssh_str=inst_o['ssh_str'],
                                status=inst_o['status'],
                                )
                instances.append(inst)
        return instances

    @classmethod
    def add_script(cls, script_path, script_name):
        files = {'script': open(f'{script_path}', 'rb'),
                 'jwt': bytes(token, 'utf-8'),
                 'user_id': bytes(user_id, 'utf-8'),
                 'filename': bytes(f'{script_name}', 'utf-8')
                 }
        resp = json.loads(post_files(files, 'addscript'))
        if resp['success']:
            return {'success': True, 'script_id': resp['script_id']}
        else:
            return {'success': False, 'error_message': resp['error_message']}

    @classmethod
    def delete_script(cls, script_id):
        return post({'jwt': token, 'id': script_id, 'user_id': user_id}, 'delscript')

    @classmethod
    def get_script(cls):
        return post({'jwt': token, 'user_id': user_id}, 'getscript')

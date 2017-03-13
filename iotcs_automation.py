import io
import os
import sys
import time
import getpass
import threading
import paramiko
import requests
import click

from subprocess import Popen, PIPE
from collections import OrderedDict


# define the global variables

PROXIES={'http':  'www-proxy.us.oracle.com',
         'https': 'www-proxy.us.oracle.com'}


class Config:
    
    remote_server_host = None
    remote_home = None
    remote_project_dir = None
    local_project_dir = None
    es_url = None
    db_connect_string = None
    
    def __init__(self):
        pass
    
    def __str__(self):
        return """\{ remote_server_host = {}, remote_home = {}, remote_project_dir = {}, local_project_dir = {},
                    es_url = {}, db_connect_str={} \}""".format(remote_server_host, remote_home, remote_project_dir, local_project_dir, es_url, db_connect_str)
    

class ESUtil:
    
    def __init__(self, es_url):
        self.es_url = es_url
    

    def get_all_es_indices(self):
        """ This returns the list of all ES indices"""
        res = requests.get('{}/_cat/indices?v'.format(self.es_url), proxies=PROXIES)
        if res.status_code != 200:
            raise Exception('Failed to get list of ES indexes')
        
        lines = res.text.strip().split('\n')
        lines = [' '.join(line.split()) for line in lines]
        rows  = [line.split(' ') for line in lines]    
        cn = rows[0].index('index')
        return [row[cn] for i, row in enumerate(rows) if i > 0]


    def delete_es_index(self, es_index):
        """ This deletes the es index and returns a boolean value of True if successful else False"""
        res = requests.delete('{}/{}'.format(self.es_url, es_index), proxies=PROXIES)
        if res.status_code != 200:
            raise Exception('Failed to delete es index {}, reason = {}'.format(es_index, res.json()['error']['type']))
        return res.json()['acknowledged']
    
        
    def delete_all_es_indices(self):
        # get all es incides
        print('#### Deleting all es indices with prefix "fm_" and "pm_" ####')
        for es_index in self.get_all_es_indices():
            if es_index.startswith('fm_') or es_index.startswith('pm_'):
                try:
                    if self.delete_es_index(es_index):
                        print('#### Deleted ES index : {} ####'.format(es_index))
                    else:
                        print('#### Failed deletion of ES index : {} ####'.format(es_index))
                except Exception as e:
                    print('Exception deleting es index. ', e)


def run_local_command(command_as_list, cwd=None):
    """
    Utility method to run any system commmand
    Returns the tuple (result, error)
    
    Arguments:
    
    command_as_list -- e.g. to run ls -al pass the list ['ls', '-al']    
    
    cwd -- The directory from which to run the command
    """
    session = Popen(command_as_list, cwd=cwd, stdin=PIPE, stdout=PIPE, stderr=PIPE)
    result, error = session.communicate()
    if result:
        result = result.decode()

    if error:
        error = error.decode()
    
    return result, error


def print_stream(stdout, stderr):
    """
    A Utility method to print the standard output & standard error on the console
    """
    def f(in_stream, out_stream):        
        for line in iter(in_stream.readline, ''):
            print(line.rstrip(), file=out_stream)
    
    t1 = threading.Thread(target=f, args=(stdout, sys.stdout))
    t2 = threading.Thread(target=f, args=(stderr, sys.stderr))
    t1.start()
    t2.start()
    
    # Now wait for the threads to complete
    t1.join()
    t2.join()


def run_ssh_command(ssh_client, command, command_dir='.'):
    """
    A Utility method to run a ssh command on the remote / devops machine
    Returns the exit status of the command
    """
    print('##### \nssh command : {} \n#####'.format(command))
    if '.' != command_dir:
        cd_command = 'cd {}'.format(command_dir)
        command = ';'.join((cd_command, command))

    stdin, stdout, stderr = ssh_client.exec_command(command)
    print_stream(stdout, stderr)
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        # command execution failed
        raise Exception('Error executing ssh command, exit code: {}'.format(exit_code))


def create_db(ssh_client, config):
    """
    Runs the dev-install.sql script against the database server
    
    Returns a boolean value of True if successful else False
    """
    command_dir = '{}/Server/Scripts/install'.format(config.remote_project_dir)
    sql_script = 'dev-install.sql'
    command = 'echo "@{}" | sqlplus {}'.format(sql_script, config.db_connect_string)
    run_ssh_command(ssh_client, command, command_dir)    


class BuildUtil:
    
    def __init__(self, config, ssh_client):
        self.config = config
        self.ssh_client = ssh_client
    
    def get_local_git_branch(self):
        """
        Returns the active branch of the local git project
        """
        command = ['git', 'branch']
        result, error = run_local_command(command, cwd=self.config.local_project_dir)
        if result:
            result = result.split('\n')
            for line in result:
                if line.startswith('*'):
                    return line[1:].strip()
    
    def get_remote_git_branch(self):
        """
        Returns the active branch of the git project on the remote / devops machine
        """
        cd_command = 'cd {}'.format(self.config.remote_project_dir)
        command = 'git branch'
        stdin,stdout,stderr = self.ssh_client.exec_command(';'.join((cd_command, command)))
        for line in stdout.readlines():
            if line.startswith('*'):
                return line[1:].strip()
        raise Exception('Failed to get remote git branch')
    
    def create_patch(self):
        """
        Creates a patch file for the local git project
        Returns the patch of the patch file as a string
        """
        command = ['git', 'diff']
        result, error = run_local_command(command, cwd=self.config.local_project_dir)
        if error:
            print('############ error ###########')
            print(error)
            return None
        if result:
    
            try:
                # create a patch directory here
                os.mkdir('patch')
            except FileExistsError:
                pass
            
            patch_file = os.path.join('patch', 'iot-cs-{}.patch'.format(int(time.time())))
            with open(patch_file, mode='w') as file:
                file.write(result)
            return patch_file    

    def copy_patch_to_remote_machine(self, filepath):
        """
        A Utility method to copy a file from the local machine to the remote / devops machine
        
        Arguments:
        
        filepath -- The path of the local file which is to be copied
        remote_path -- The path on the remote / devops machine where the file is to be copied
        """
        with self.ssh_client.open_sftp() as sftp:
            remote_dir = '{}/patch'.format(self.config.remote_home)
            
            try:
                # create the patch dir
                sftp.mkdir(remote_dir)
            except OSError:
                pass
            
            filename = filepath.split('/')[-1]
            remote_filepath = '{}/{}'.format(remote_dir, filename)
            sftp.put(filepath, remote_filepath)
            print('Copied patch file from {} to remote server location {}'.format(filepath, remote_filepath))
            return remote_filepath

    def apply_patch(self, patch_file, project_dir, git_branch):
        # do git reset
        command = 'git reset --hard'
        run_ssh_command(self.ssh_client, command, self.config.remote_project_dir)           
    
        # check if the current branch is not the target branch then checkout the target branch
        current_git_branch = self.get_remote_git_branch()
        print('Remote git branch is : {}'.format(current_git_branch))
        
        if git_branch != current_git_branch:
            # do a git pull
            command = 'git pull'
            run_ssh_command(self.ssh_client, command, self.config.remote_project_dir)
            
            # checkout branch
            command = 'git checkout {}'.format(git_branch)
            run_ssh_command(self.ssh_client, commmand, self.config.remote_project_dir)
    
        # do a git pull
        command = 'git pull'
        run_ssh_command(self.ssh_client, command, self.config.remote_project_dir)
    
        if patch_file:
            # do git apply patch
            print('Applying patch:\n======================')
            command = 'git apply {}'.format(patch_file)
            run_ssh_command(self.ssh_client, command, self.config.remote_project_dir)    

    def build_project(self):        
        # do assemble prepareBundles
        print('Building project:\n====================')
        command = './gradlew assemble prepareBundles'
        run_ssh_command(self.ssh_client, command, self.config.remote_project_dir)
        print('########## Build project completed Successfully #########')

    def deploy_project(self):
        print('Deploy wars:\n========================')
        # update the datasource.properties file
        cwd = self.config.remote_project_dir + '/' + 'build/bundles/IoTServer'    
    
        with self.ssh_client.open_sftp() as sftp:        
            sftp.chdir(cwd)
            
            if 'datasource.properties.backup' in sftp.listdir():
                sftp.remove('datasource.properties.backup')
                
            properties = OrderedDict()
            with sftp.open('datasource.properties', mode='r') as datasource_file:
                for line in datasource_file:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('#'):
                        continue
                    t = line.split('=')
                    properties[t[0]] = t[1]
            
            properties['admin.port'] = '7001'
            properties['admin.password'] = 'Welcome1'
            
            sftp.rename('datasource.properties', 'datasource.properties.backup')
            
            with sftp.open('datasource.properties', mode='w') as new_datasource_file:
                for key in properties:
                    line = '{}={}'.format(key, properties[key])
                    new_datasource_file.write(line)
                    new_datasource_file.write('\n')
    
        # run deploywars.sh        
        command = 'sh deploywars.sh'
        run_ssh_command(self.ssh_client, command, cwd)
        print('########## deploywars completed Successfully #########')


def move_files(predicate, target_dir):
    """
    Moves all files from the current directory which satisfy the predicate to the target directory
    
    Arguments:
    
    predicate -- e.g. lambda x: x.endswith('.log')
    target_dir -- e.g. 'logs'
    """
    try:
        os.makedirs(target_dir)
    except FileExistsError:
        pass
    
    for file in os.listdir('.'):
        if predicate(file):
            os.rename(file, os.path.join(target_dir, file))


def main(ssh_client, config, drop_and_create_db=False, drop_es_indices=False):
    
    build_util = BuildUtil(config, ssh_client)
    
    patch_file = build_util.create_patch()
    print('Patch file created = ', patch_file)
    
    remote_patch_file_path = None
        
    if patch_file:
        # copy patch file to remote server            
        remote_patch_file_path = build_util.copy_patch_to_remote_machine(patch_file)
    else:
        print('No git diff found')

    # apply the patch
    active_git_branch = build_util.get_local_git_branch()
    print('Local git branch is : {}'.format(active_git_branch))
    
    build_util.apply_patch(remote_patch_file_path, config.remote_project_dir, active_git_branch)
    build_util.build_project()    
        
    if drop_and_create_db:
        print('#### Running task to drop and create db ####')
        create_db(ssh_client, config)
        
    if drop_es_indices:
        print('#### Running task to drop all ES indices ####')
        es_util = ESUtil(config.es_url)
        es_util.delete_all_es_indices()

    build_util.deploy_project()
    
    # Moves all log files to the logs directory
    move_files(predicate=lambda x: x.endswith('.log'), target_dir='./logs')    
        
        

@click.command()
@click.option('--remotehost', help='The hostname of the target / remote machine')
@click.option('--local-proj-dir', help='The path to the iotcs project directory on the local machine')
@click.option('--remote-proj-dir', help='The path to the iotcs project directory on the remote machine')
@click.option('--db-url', help='The database connection url, if provided will run drop and create on the db')
@click.option('--es-url', help='The es connection url, if provided will drop all fm and pm indexes')
def cli(remotehost, local_proj_dir, remote_proj_dir, db_url, es_url):
    """
    This script creates a git patch of your local iot-cs project.\n
    Copies and applies the patch on your remote / devops server.\n
    Builds the iotcs project and deploys the Server-All.ear on the remote / devops machine Weblogic AdminServer.\n
    Drop and create the database tables using dev-install.sql\n
    Drop the ES indexes    
    """
        
    config = Config()
    config.remote_server_host = remotehost
    config.local_project_dir = local_proj_dir
    config.remote_project_dir = remote_proj_dir
    config.es_url = es_url
    config.db_connect_string = db_url    
        
    drop_and_create_db, drop_es_indices = bool(db_url), bool(es_url)    
    
    # get username and password of devops / remote machine
    username = getpass.getpass('Enter User: ')
    password = getpass.getpass('Enter Password for User {}: '.format(username))
    
    config.remote_home = os.path.join('/scratch', username)
    
    click.echo('remote host : {}'.format(config.remote_server_host))
    click.echo('local project directory : {}'.format(config.local_project_dir))
    click.echo('remote project directory : {}'.format(config.remote_project_dir))
    click.echo('es_url : {}'.format(config.es_url))
    click.echo('db_connect_string : {}'.format(config.db_connect_string))    
    click.echo('remote home : {}'.format(config.remote_home))
    
    ssh_client = paramiko.SSHClient()
    ssh_client.load_system_host_keys()
    
    try:
        ssh_client.connect(config.remote_server_host, username=username, password=password)
        click.echo('SSH connection established with remote host : {}'.format(config.remote_server_host))        
        main(ssh_client, config, drop_and_create_db, drop_es_indices)
    except paramiko.SSHException as e1:
        click.echo('Connection Error: ', e1)
        raise e;
    except Exception as e2:
        click.echo('Failed with Error: ', e2)
    finally:
        ssh_client.close()


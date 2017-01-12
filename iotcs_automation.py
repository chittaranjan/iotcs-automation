import io
import os
import sys
import time
import getpass
import threading
import paramiko
import requests
import click

from io import StringIO
from multiprocessing import Process, Semaphore
from subprocess import Popen, PIPE
from collections import OrderedDict


# define the global variables

SSH_CLIENT = None
REMOTE_SERVER_HOST = None
REMOTE_HOME = None
REMOTE_PROJECT_DIR = None
LOCAL_PROJECT_DIR = None
ES_URL = None
DB_CONNECT_STRING = None

INTERVAL_SECS = 3

PROXIES={'http':  'www-proxy.us.oracle.com',
         'https': 'www-proxy.us.oracle.com'}


def run_command(command_as_list, cwd=None):
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


def run_sql_query(sql_command):
    """
    Utility method to run a SQL command using SQLPLUS program. So it assumes that SQLPLUS is installed in the local system
    Returns the tuple (result, error)
    
    Arguments:
    
    sql_command -- e.g. 'SELECT * FROM FM_RULE_REACH_RECORD'
    """
    session = Popen(['sqlplus', '-S', DB_CONNECT_STRING], stdin=PIPE, stdout=PIPE, stderr=PIPE)
    print('Connected to db: {}'.format(DB_CONNECT_STRING))
    print('Executing command: {}'.format(sql_command))

    start_time = time.time()
    session.stdin.write((sql_command + '\n').encode())
    session.stdin.flush()

    result, error = session.communicate()    
    print('Command execution completed in %.2f secs' % (time.time() - start_time))

    if result:
        result = result.decode()

    if error:
        error = error.decode()
    
    return result, error


def copy_patch_to_remote_machine(filepath):
    """
    A Utility method to copy a file from the local machine to the remote / devops machine
    
    Arguments:
    
    filepath -- The path of the local file which is to be copied
    remote_path -- The path on the remote / devops machine where the file is to be copied
    """
    with SSH_CLIENT.open_sftp() as sftp:
        remote_dir = '{}/patch'.format(REMOTE_HOME)
        
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
        


def apply_ssh_command(command):
    """
    A Utility method to run a ssh command on the remote / devops machine
    Returns the exit status of the command
    """
    stdin, stdout, stderr = SSH_CLIENT.exec_command(command)
    print_stream(stdout, stderr)
    return stdout.channel.recv_exit_status()


def print_stream(stdout, stderr):
    """
    A Utility method to print the standard output & standard error on the console
    """
    def f(in_stream, out_stream):        
        for line in iter(in_stream.readline, ''):
            print(line.rstrip(), file=out_stream)
    
    #f(stdout, sys.stdout)
    #f(stderr, sys.stderr)
    t1 = threading.Thread(target=f, args=(stdout, sys.stdout))
    t2 = threading.Thread(target=f, args=(stderr, sys.stderr))
    t1.start()
    t2.start()
    
    # Now wait for the threads to complete
    t1.join()
    t2.join()


def create_db():
    """
    Runs the dev-install.sql script against the remote / devops database server
    
    Returns a boolean value of True if successful else False
    """    
    sql_command = '@{}/Server/Scripts/dev-install.sql'.format(LOCAL_PROJECT_DIR)
    result, error = run_sql_query(sql_command)

    if result:
        print('############ result ###########')
        print(result)

    if error:
        print('############ error ###########')        
        print(error)

    is_successful = not error
    return is_successful


def get_all_es_indices():
    """ This returns the list of all ES indices"""
    res = requests.get('{}/_cat/indices?v'.format(ES_URL), proxies=PROXIES)
    if res.status_code != 200:
        raise Exception('Failed to get list of ES indexes')
    
    lines = res.text.strip().split('\n')
    lines = [' '.join(line.split()) for line in lines]
    rows  = [line.split(' ') for line in lines]    
    cn = rows[0].index('index')
    return [row[cn] for i, row in enumerate(rows) if i > 0]


def delete_es_index(es_index):
    """ This deletes the es index and returns a boolean value of True if successful else False"""
    res = requests.delete('{}/{}'.format(ES_URL, es_index), proxies=PROXIES)
    if res.status_code != 200:
        raise Exception('Failed to delete es index {}, reason = {}'.format(es_index, res.json()['error']['type']))
    return res.json()['acknowledged']

    
def delete_all_es_indices():
    # get all es incides
    print('#### Deleting all es indices with prefix "fm_" and "pm_" ####')
    for es_index in get_all_es_indices():
        if es_index.startswith('fm_') or es_index.startswith('pm_'):
            try:
                if delete_es_index(es_index):
                    print('#### Deleted ES index : {} ####'.format(es_index))
                else:
                    print('#### Failed deletion of ES index : {} ####'.format(es_index))
            except Exception as e:
                print('Exception deleting es index. ', e)


def create_patch():
    """
    Creates a patch file for the local git project
    Returns the patch of the patch file as a string
    """
    command = ['git', 'diff']
    result, error = run_command(command, cwd=LOCAL_PROJECT_DIR)
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


def get_local_git_branch():
    """
    Returns the active branch of the local git project
    """
    command = ['git', 'branch']
    result, error = run_command(command, cwd=LOCAL_PROJECT_DIR)
    if result:
        result = result.split('\n')
        for line in result:
            if line.startswith('*'):
                return line[1:].strip()


def get_remote_git_branch():
    """
    Returns the active branch of the git project on the remote / devops machine
    """
    cd_command = 'cd {}'.format(REMOTE_PROJECT_DIR)
    command = 'git branch'
    stdin,stdout,stderr = SSH_CLIENT.exec_command(';'.join((cd_command, command)))
    for line in stdout.readlines():
        if line.startswith('*'):
            return line[1:].strip()
    raise Exception('Failed to get remote git branch')


def apply_patch(patch_file, project_dir, git_branch):
    cd_command = 'cd {}'.format(REMOTE_PROJECT_DIR)

    # do git reset
    command = 'git reset --hard'
    exit_code = apply_ssh_command(';'.join((cd_command, command)))
    if exit_code < 0:
        raise Exception('Failed executing command: {}'.format(command))

    # check if the current branch is not the target branch then checkout the target branch
    current_git_branch = get_remote_git_branch()
    print('Remote git branch is : {}'.format(current_git_branch))
    
    if git_branch != current_git_branch:        
        command = 'git checkout {}'.format(git_branch)
        print(command)
        exit_code = apply_ssh_command(';'.join((cd_command, command)))
        if exit_code < 0:
            raise Exception('Failed executing command: {}'.format(command))

    # do a git pull
    command = 'git pull'
    exit_code = apply_ssh_command(';'.join((cd_command, command)))

    if patch_file:
        # do git apply patch
        print('Applying patch:\n======================')
        command = 'git apply {}'.format(patch_file)
        exit_code = apply_ssh_command(';'.join((cd_command, command)))
        if exit_code:
            raise Exception('Failed executing command: {}'.format(command))
    


def build_project():
    cd_command = 'cd {}'.format(REMOTE_PROJECT_DIR)
    # do assemble prepareBundles
    print('Building project:\n====================')
    command = './gradlew assemble prepareBundles'
    exit_code = apply_ssh_command(';'.join((cd_command, command)))
    if exit_code < 0:
        raise Exception('Failed executing command: {}'.format(command))
    print('########## Build project completed Successfully #########')


def deploy_project():
    print('Deploy wars:\n========================')
    # update the datasource.properties file
    cwd = REMOTE_PROJECT_DIR + '/' + 'build/bundles/IoTServer'    

    with SSH_CLIENT.open_sftp() as sftp:        
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
    cd_command = 'cd {}'.format(cwd)
    command = 'sh deploywars.sh'
    exit_code = apply_ssh_command(';'.join((cd_command, command)))
    if exit_code < 0:
        raise Exception('Failed executing command: {}'.format(command))
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


def _perform_task(task, task_name, mutex):    
    output, error = StringIO(), StringIO()
    
    t1, t2 = sys.stdout, sys.stderr   
    sys.stdout, sys.stderr = output, error    
        
    task()
        
    sys.stdout, sys.stderr = t1, t2
    
    # wait for all other processes to finish before printing the output    
    mutex.acquire()
    try:
        # sleep for 3 seconds to give a visual illusion of running process
        print('############### {} output ################'.format(task_name))
        time.sleep(INTERVAL_SECS)        
        print_stream(output, error)
    finally:
        mutex.release()


def main(drop_and_create_db=False, drop_es_indices=False):
    # create the db
    # this can take sometime, so do it in a separate process
    
    mutex = Semaphore(0)
    
    p1_name = 'DB drop and create'
    p2_name = 'Drop ES indices'
    p1 = Process(target=_perform_task, args=(create_db, p1_name, mutex)) if drop_and_create_db else None
    p2 = Process(target=_perform_task, args=(delete_all_es_indices, p2_name, mutex)) if drop_es_indices else None    
    
    try:        
        if p1:
            p1.start()
            print('#### Started process : {} ####'.format(p1_name))
        
        if p2:
            p2.start()
            print('#### Started process : {} ####'.format(p2_name))        
        
        patch_file = create_patch()
        print('Patch file = ', patch_file)
        remote_patch_file_path = None
        
        if patch_file:
            # copy patch file to remote server            
            remote_patch_file_path = copy_patch_to_remote_machine(patch_file)
        else:
            print('No git diff found')

        # apply the patch
        active_git_branch = get_local_git_branch()
        print('Local git branch is : {}'.format(active_git_branch))
        #apply_patch(remote_patch_file_path, REMOTE_PROJECT_DIR, active_git_branch)
        #build_project()
        #deploy_project()
        
        # Moves all log files to the logs directory
        move_files(predicate=lambda x: x.endswith('.log'), target_dir='./logs')
    finally:
        mutex.release()
        if p1:
            print('Wait for process : {} to complete'.format(p1_name))
            p1.join()
            
        if p2:
            print('Wait for process : {} to complete'.format(p2_name))
            p2.join()
        

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
    global REMOTE_SERVER_HOST, REMOTE_PROJECT_DIR, LOCAL_PROJECT_DIR, REMOTE_HOME, ES_URL, DB_CONNECT_STRING, SSH_CLIENT
    print('Hello World!')
    
    REMOTE_SERVER_HOST = remotehost
    LOCAL_PROJECT_DIR = local_proj_dir
    REMOTE_PROJECT_DIR = remote_proj_dir
    ES_URL = es_url
    DB_CONNECT_STRING = db_url
    
    drop_and_create_db, drop_es_indices = bool(db_url), bool(es_url)
    
    click.echo('remote host : {}'.format(REMOTE_SERVER_HOST))
    click.echo('local project directory : {}'.format(LOCAL_PROJECT_DIR))
    click.echo('remote project directory : {}'.format(REMOTE_PROJECT_DIR))
    click.echo('es_url : {}'.format(ES_URL))
    click.echo('db_connect_string : {}'.format(DB_CONNECT_STRING))
    # get username and password of devops / remote machine
    username = getpass.getpass('Enter User: ')
    password = getpass.getpass('Enter Password for User {}: '.format(username))
    
    REMOTE_HOME = os.path.join('/scratch', username)
    click.echo('remote home : {}'.format(REMOTE_HOME))
    
    SSH_CLIENT = paramiko.SSHClient()
    SSH_CLIENT.load_system_host_keys()
    
    try:
        SSH_CLIENT.connect(REMOTE_SERVER_HOST, username=username, password=password)
        click.echo('SSH connection established with remote host : {}'.format(REMOTE_SERVER_HOST))        
        main(drop_and_create_db, drop_es_indices)
    except paramiko.SSHException as e1:
        click.echo('Connection Error: ', e1)
        raise e;
    except Exception as e2:
        click.echo('Failed with Error: ', e2)
    finally:
        SSH_CLIENT.close()


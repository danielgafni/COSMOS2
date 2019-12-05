import pprint
import random
import re
import string
import time

import boto3

from cosmos.api import TaskStatus
from cosmos.job.drm.DRM_Base import DRM


def random_string(length):
    return ''.join([random.choice(string.ascii_letters + string.digits) for _ in range(length)])


def split_bucket_key(s3_uri):
    """
    >>> split_bucket_key('s3://bucket/path/to/fname')
    ('bucket', 'path/to/fname')
    """
    bucket, key = re.search('s3://(.+?)/(.+)', s3_uri).groups()
    if key == '':
        raise ValueError('no prefix in %s' % s3_uri)
    return bucket, key


def submit_script_as_aws_batch_job(local_script_path,
                                   s3_prefix_for_command_script_temp_files,
                                   job_name,
                                   container_image,
                                   job_queue,
                                   instance_type=None,
                                   memory=1024,
                                   vcpus=1):
    """
    :param local_script_path: the local path to a script to run in awsbatch.
    :param s3_prefix_for_command_script_temp_files: the s3 bucket to use for storing the local script to to run.  Caller
      is responsible for cleaning it up.
    :param job_name: name of the job_dict.
    :param container_image: docker image.
    :param memory: amount of memory to reserve.
    :param vcpus: amount of vcpus to reserve.
    :return: obId, job_definition_arn, s3_command_script_uri.
    """
    if s3_prefix_for_command_script_temp_files.endswith('/'):
        raise ValueError('s3_prefix_for_command_script_temp_files should not have a ' \
                         'trailing slash.  It is set to %s' % s3_prefix_for_command_script_temp_files)
    batch = boto3.client(service_name="batch")
    s3 = boto3.client(service_name="s3")

    key = random_string(32) + '.txt'
    s3.upload_file(local_script_path, s3_prefix_for_command_script_temp_files, key)
    s3_command_script_uri = 's3://{s3_prefix_for_command_script_temp_files}/{key}'.format(
        s3_prefix_for_command_script_temp_files=s3_prefix_for_command_script_temp_files,
        key=key)

    command = 'aws s3 cp --quiet {s3_command_script_uri} command_script && ' \
              'chmod +x command_script && ' \
              './command_script'
    command = command.format(**locals())

    container_properties = {
        "image": container_image,
        "jobRoleArn": "ecs_administrator",
        "mountPoints": [{"containerPath": "/scratch",
                         "readOnly": False,
                         "sourceVolume": "scratch"}],
        "volumes": [{"name": "scratch", "host": {"sourcePath": "/scratch"}}],
        "resourceRequirements": [],
        # run_s3_script
        "command": ['bash', '-c', '%s' % command]
    }
    if memory is not None:
        container_properties["memory"] = memory
    if vcpus is not None:
        container_properties['vcpus'] = vcpus
    if instance_type is not None:
        container_properties['instanceType'] = instance_type

    resp = batch.register_job_definition(
        jobDefinitionName=job_name,
        type='container',
        containerProperties=container_properties
    )
    _check_aws_response_for_error(resp)
    job_definition_arn = resp['jobDefinitionArn']

    submit_jobs_response = batch.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition_arn
    )
    jobId = submit_jobs_response['jobId']

    return jobId, job_definition_arn, s3_command_script_uri


def get_logs(log_stream_name, attempts=6, sleep_between_attempts=10):
    logs_client = boto3.client(service_name="logs")
    try:
        response = logs_client.get_log_events(
            logGroupName='/aws/batch/job_dict',
            logStreamName=log_stream_name,
            startFromHead=True)
        _check_aws_response_for_error(response)
        return '\n'.join(d['message'] for d in response['events'])
    except logs_client.exceptions.ResourceNotFoundException:
        if attempts == 0:
            return 'log stream not found for log_stream_name: %s\n' % log_stream_name
        else:
            time.sleep(sleep_between_attempts)
            return get_logs(log_stream_name, attempts=attempts - 1)


def get_aws_batch_job_infos(job_ids):
    batch_client = boto3.client(service_name="batch")
    describe_jobs_response = batch_client.describe_jobs(jobs=job_ids)
    _check_aws_response_for_error(describe_jobs_response)
    return describe_jobs_response['jobs']


class DRM_AWSBatch(DRM):
    name = 'awsbatch'
    required_drm_options = {'container_image',
                            's3_prefix_for_command_script_temp_files'}

    _batch_client = None
    _s3_client = None

    def __init__(self):
        self.job_id_to_s3_script_uri = dict()
        super(DRM_AWSBatch, self).__init__()

    @property
    def batch_client(self):
        if self._batch_client is None:
            self._batch_client = boto3.client(service_name="batch")
        return self._batch_client

    @property
    def s3_client(self):
        if self._s3_client is None:
            self._s3_client = boto3.client(service_name="s3")
        return self._s3_client

    def submit_job(self, task):
        jobId, job_definition_arn, s3_command_script_uri = submit_script_as_aws_batch_job(
            local_script_path=task.output_command_script_path,
            s3_prefix_for_command_script_temp_files=task.drm_options['s3_prefix_for_command_script_temp_files'],
            container_image=task.drm_options['container_image'],
            job_name='cosmos-task',
            job_queue=task.queue,
            memory=task.mem_req,
            vcpus=task.cpu_req,
            instance_type=task.drm_options.get('instance_type'))

        # just save pointer to logstream.  We'll collect them when the job finishes.
        job_dict = get_aws_batch_job_infos([jobId])[0]
        with open(task.output_stdout_path, 'w'):
            pass
        with open(task.output_stderr_path, 'w') as fp:
            fp.write(pprint.pformat(job_dict, indent=2))

        # set task attributes
        task.drm_jobID = jobId
        task.status = TaskStatus.submitted
        task.s3_command_script_uri = s3_command_script_uri
        task.job_definition_arn = job_definition_arn

    def filter_is_done(self, tasks):
        job_ids = [task.drm_jobID for task in tasks]
        jobs = get_aws_batch_job_infos(job_ids)
        for task, job_dict in zip(tasks, jobs):
            if job_dict['status'] in ['SUCCEEDED', 'FAILED']:
                # get exit status
                if 'attempts' in job_dict:
                    # exit code might be missing if for example the instance was terminated because the compute
                    # environment was deleted.
                    exit_status = job_dict['attempts'][-1]['container'].get('exitCode', -2)
                else:
                    exit_status = -1

                self._cleanup_task(task, job_dict['container']['logStreamName'])

                yield task, dict(exit_status=exit_status,
                                 wall_time=job_dict['stoppedAt'] - job_dict['stoppedAt'])

    def _cleanup_task(self, task, log_stream_name=None):
        # if log_stream_name wasn't passed in, query aws to get it
        if log_stream_name is None:
            job_dict = get_aws_batch_job_infos([task.drm_jobID])
            log_stream_name = job_dict[0]['container'].get('logStreamName')

        if log_stream_name is None:
            logs = 'no log stream was available for job: %s\n' % task.drm_jobID
        else:
            # write logs to stdout
            logs = get_logs(log_stream_name=log_stream_name)

        with open(task.output_stdout_path, 'w') as fp:
            fp.write(logs)

        # delete temporary s3 script path
        bucket, key = split_bucket_key(task.s3_command_script_uri)
        self.s3_client.delete_object(Bucket=bucket, Key=key)

        # deregister job definition
        self.batch_client.deregister_job_definition(jobDefinition=task.job_definition_arn)

    def drm_statuses(self, tasks):
        """
        :returns: (dict) task.drm_jobID -> drm_status
        """
        job_ids = [task.drm_jobID for task in tasks]
        return dict(zip(job_ids, get_aws_batch_job_infos(job_ids)))

    def kill(self, task):
        batch_client = boto3.client(service_name="batch")
        terminate_job_response = batch_client.terminate_job(jobId=task.drm_jobID,
                                                            reason='terminated by cosmos')
        _check_aws_response_for_error(terminate_job_response)

        self._cleanup_task(task)


class JobStatusError(Exception):
    pass


def _check_aws_response_for_error(r):
    if 'failures' in r and len(r['failures']):
        raise Exception('Failures:\n{0}'.format(pprint.pformat(r, indent=2)))

    status_code = r['ResponseMetadata']['HTTPStatusCode']
    if status_code != 200:
        raise Exception(
            'Task status request received status code {0}:\n{1}'.format(status_code, pprint.pformat(r, indent=2)))
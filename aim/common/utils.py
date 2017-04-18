# Copyright (c) 2016 Cisco Systems
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import base64
import functools
import hashlib
import os
import random
import re
import time
import traceback
import uuid

import gevent
from oslo_config import cfg
from oslo_log import log as logging

LOG = logging.getLogger(__name__)
AIM_LOCK_PREFIX = 'aim_lock'
OPENSTACK_VMM_TYPE = 'OpenStack'


def log(method):
    """Decorator helping to log method calls."""
    _LOG = logging.getLogger(method.__module__)

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        instance = args[0]
        data = {"class_name": "%s.%s" % (instance.__class__.__module__,
                                         instance.__class__.__name__),
                "method_name": method.__name__,
                "args": args[1:], "kwargs": kwargs}
        _LOG.debug('%(class_name)s method %(method_name)s'
                   ' called with arguments %(args)s %(kwargs)s', data)
        return method(*args, **kwargs)
    return wrapper


def generate_uuid():
    return str(uuid.uuid4())


def sleep(time_in_seconds):
    gevent.sleep(time_in_seconds)


def wait_for_next_cycle(start_time, polling_interval, log, readable_caller='',
                        notify_exceeding_timeout=True):
    # sleep till end of polling interval
    elapsed = time.time() - start_time
    log.debug("%(caller)s loop - completed in %(time).3f. ",
              {'caller': readable_caller, 'time': elapsed})
    if elapsed < polling_interval:
        sleep(polling_interval - elapsed)
    elif notify_exceeding_timeout:
        log.debug("Loop iteration exceeded interval "
                  "(%(polling_interval)s vs. %(elapsed)s)!",
                  {'polling_interval': polling_interval,
                   'elapsed': elapsed})
        sleep(0)
    else:
        sleep(0)


class Counter(object):

    def __init__(self):
        self.num = 0

    def get(self):
        return self.num

    def increment(self):
        self.num += 1


def exponential_backoff(max_time, tentative=None):
    tentative = tentative or Counter()
    sleep_time_secs = min(random.random() * (2 ** tentative.get()), max_time)
    LOG.debug('Sleeping for %s seconds' % sleep_time_secs)
    sleep(sleep_time_secs)
    tentative.increment()
    return tentative


def perform_harakiri(log, message=None):
    log.error("AAAAARGH!")
    if message:
        log.error(message)
    if cfg.CONF.aim.recovery_restart:
        os._exit(1)


def stob(s):
    if s.lower() in ['true', 'yes', 't', 'y', '1']:
        return True
    if s.lower() in ['false', 'no', 'f', 'n', '0']:
        return False
    return None


def camel_to_snake(name):
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def snake_to_lower_camel(name):
    split = name.split('_')
    return split[0] + ''.join(word.capitalize() for word in split[1:])


def sanitize_name(type, *args):
    h = hashlib.sha256()
    h.update(type)
    h.update('\x00')
    for component in args:
        h.update(component)
        h.update('\x00')
    return base64.b32encode(h.digest()).rstrip('=').lower()


class ThreadExit(Exception):
    pass


def retry_loop(max_wait, max_retries, name):
    def wrap(func):
        def inner(*args, **kwargs):
            recovery_retries = None
            while True:
                try:
                    func(*args, **kwargs)
                    recovery_retries = None
                except (gevent.GreenletExit, ThreadExit) as e:
                    raise e
                except Exception as e:
                    LOG.error(traceback.format_exc())
                    recovery_retries = exponential_backoff(
                        max_wait, tentative=recovery_retries)
                    if recovery_retries.get() >= max_retries:
                        LOG.error("Exceeded max recovery retries for %s", name)
                        raise e
        return inner
    return wrap


class FakeContext(object):

    def __init__(self, store=None):
        if store:
            self.store = store

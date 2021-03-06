import errno
import gzip
import json
import logging
import os
import platform
import shutil
import subprocess
import time

import requests

from progressbar import Bar, Timer, FileTransferSpeed, ProgressBar

LOG = logging.getLogger('mozci')


def path_to_file(filename):
    """Add files to .mozilla/mozci"""
    path = os.path.expanduser('~/.mozilla/mozci/')
    if not os.path.exists(path):
        os.makedirs(path)
    filepath = os.path.join(path, filename)
    return filepath


class DownloadProgressBar(ProgressBar):
    """
    Helper class to show download progress.
    """
    def __init__(self, filename, size):
        widgets = [
            os.path.basename(filename), ": ",
            Bar(marker=">", left="[", right="]"), ' ',
            Timer(), ' ',
            FileTransferSpeed(), " ",
            "{0}MB".format(round(size / 1024 / 1024, 2))
        ]
        super(DownloadProgressBar, self).__init__(widgets=widgets, maxval=size)


def _load_json_file(filepath):
    '''
    This is a helper function to load json contents from a file
    '''
    LOG.debug("About to load %s." % filepath)

    # Sniff whether the file is gzipped
    fd = open(filepath, 'r')
    magic = fd.read(2)
    fd.seek(0)

    if magic == '\037\213':  # gzip magic number
        if platform.system() == 'Windows':
            # Windows doesn't like multiple processes opening the same files
            fd.close()
            # Issue 202 - gzip.py on Windows does not handle big files well
            cmd = ["gzip", "-cd", filepath]
            LOG.debug("-> %s" % ' '.join(cmd))
            try:
                data = subprocess.check_output(cmd)
            except subprocess.CalledProcessError, e:
                if e.errno == errno.ENOENT:
                    raise Exception(
                        "You don't have gzip installed on your system. "
                        "Please install it. You can find it inside of mozilla-build."
                    )
        else:
            gzipper = gzip.GzipFile(fileobj=fd)
            data = gzipper.read()
            gzipper.close()

    else:
        data = fd.read()

    if platform.system() != 'Windows':
        fd.close()

    try:
        return json.loads(data)
    except ValueError, e:
        LOG.exception(e)
        new_file = filepath + ".corrupted"
        shutil.move(filepath, new_file)
        LOG.error("The file on-disk does not have valid data")
        LOG.info("We have moved %s to %s for inspection." % (filepath, new_file))
        exit(1)


def load_file(filename, url):
    """
    We download a file without decompressing it so we can keep track of its progress.
    We save it to disk and return the contents of it.
    We also check if the file on the server is newer to determine if we should download it again.

    raises Exception if anything goes wrong.
    """
    # Obtain the absolute path to our file in the cache
    if not os.path.isabs(filename):
        filepath = path_to_file(filename)
    else:
        filepath = filename

    headers = {
        'Accept-Encoding': None,
    }

    existed = os.path.exists(filepath)
    if existed:
        # The file exists in the cache, let's verify that is still current
        statinfo = os.stat(filepath)
        last_mod_date = time.strftime('%a, %d %b %Y %H:%M:%S GMT',
                                      time.gmtime(statinfo.st_mtime))
        headers['If-Modified-Since'] = last_mod_date
    else:
        # The file does not exist in the cache; let's fetch
        LOG.debug("We have not been able to find %s on disk." % filepath)

    req = requests.get(url, stream=True, timeout=(8, 24), headers=headers)

    if req.status_code == 200:
        if existed:
            # The file on the server is newer
            LOG.debug("The local file was last modified at %s. "
                      "We need to fetch it again." % last_mod_date)

        LOG.debug("About to fetch %s from %s" % (filename, req.url))
        size = int(req.headers['Content-Length'].strip())
        pbar = DownloadProgressBar(filepath, size).start()
        bytes = 0
        with open(filepath, 'w') as fd:
            for chunk in req.iter_content(10 * 1024):
                if chunk:  # filter out keep-alive new chunks
                    fd.write(chunk)
                    bytes += len(chunk)
                    pbar.update(bytes)
        pbar.finish()

    elif req.status_code == 304:
        # The file on disk is recent
        LOG.debug("%s is on disk and it is current." % last_mod_date)

    else:
        raise Exception("We received %s which is unexpected." % req.status_code)

    return _load_json_file(filepath)

"""Trusted operator-side Word executor for one explicitly owned local Worker.

This process runs on the host. It never listens on a socket, accepts a command,
or discovers containers. Client cancellation revokes publication; an in-flight
provider remains bounded by its existing timeout and cleans up before returning.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ID = re.compile(r"[0-9a-f]{32}\Z")
_CONTAINER = re.compile(r"[0-9a-f]{64}\Z")
_INSPECT = (
    '{"Id":{{json .Id}},"Running":{{json .State.Running}},'
    '"User":{{json .Config.User}},"Labels":{{json .Config.Labels}},'
    '"Privileged":{{json .HostConfig.Privileged}},'
    '"ReadonlyRootfs":{{json .HostConfig.ReadonlyRootfs}},'
    '"Tmpfs":{{json .HostConfig.Tmpfs}},"Mounts":{{json .Mounts}}}'
)

# Fixed program, isolated interpreter, no imports from the peer's application.
# All filesystem operations are relative to pinned, no-follow directory fds.
HELPER = r"""
import json,os,re,stat,sys
LIMIT_REQUEST=160*1024*1024
LIMIT_RESPONSE=200*1024*1024
ID=re.compile(r"[0-9a-f]{32}\Z")

def actual_tmpfs():
    if os.geteuid()!=65532 or os.getegid()!=65532:raise ValueError()
    with open('/proc/self/mountinfo',encoding='utf-8') as stream:lines=stream.read().splitlines()
    for target in ('/tmp','/tmp/word-execution'):
        matches=[]
        for line in lines:
            before,separator,after=line.partition(' - ');fields=before.split()
            if not separator or len(fields)<5:continue
            mount=re.sub(r'\\([0-7]{3})',lambda item:chr(int(item[1],8)),fields[4])
            if target==mount or target.startswith(mount.rstrip('/')+'/'):
                matches.append((len(mount),after.split()[0]))
        if not matches or max(matches)[1]!='tmpfs':raise ValueError()

def private(info,directory=False):
    if info.st_uid!=65532 or stat.S_IMODE(info.st_mode)!=(0o700 if directory else 0o600):raise ValueError()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):raise ValueError()
    if not directory and info.st_nlink!=1:raise ValueError()

def root():
    parent=os.open('/tmp',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:fd=os.open('word-execution',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    finally:os.close(parent)
    try:private(os.fstat(fd),True)
    except BaseException:os.close(fd);raise
    return fd

def job(parent,identity):
    if not ID.fullmatch(identity):raise ValueError()
    fd=os.open(identity,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    try:private(os.fstat(fd),True)
    except BaseException:os.close(fd);raise
    return fd

def info(directory,name,maximum):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    try:
        value=os.fstat(fd);private(value)
        if value.st_size>maximum:raise ValueError()
        return value
    finally:os.close(fd)

def present(directory,name,maximum):
    try:info(directory,name,maximum);return True
    except FileNotFoundError:return False

def signature(value):
    return [value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns]

def linked(parent,directory,identity):
    try:value=os.stat(identity,dir_fd=parent,follow_symlinks=False)
    except FileNotFoundError:return False
    private(value,True)
    actual=os.fstat(directory)
    return (value.st_dev,value.st_ino)==(actual.st_dev,actual.st_ino)

def active(parent,directory,identity,token):
    if not linked(parent,directory,identity):return False
    if [os.fstat(directory).st_dev,os.fstat(directory).st_ino]!=token['job']:return False
    if present(directory,'cancel',0):return False
    if present(directory,'response.json',LIMIT_RESPONSE):return False
    try:return signature(info(directory,'request.json',LIMIT_REQUEST))==token['request']
    except FileNotFoundError:return False

def main():
    action=sys.argv[1]
    actual_tmpfs()
    if action=='verify':sys.stdout.write('verified');return
    try:parent=root()
    except FileNotFoundError:
        if action=='list':sys.stdout.write('null');return
        raise
    try:
        if action=='list':
            identities=[]
            with os.scandir(parent) as entries:
                for count,entry in enumerate(entries):
                    if count>=256:raise ValueError()
                    if ID.fullmatch(entry.name):identities.append(entry.name)
                    elif entry.name!='.client.lock':raise ValueError()
            for identity in sorted(identities):
                directory=job(parent,identity)
                try:
                    if present(directory,'cancel',0) or present(directory,'response.json',LIMIT_RESPONSE):continue
                    if present(directory,'request.json',LIMIT_REQUEST):sys.stdout.write(json.dumps(identity));return
                finally:os.close(directory)
            sys.stdout.write('null');return
        identity=sys.argv[2];directory=job(parent,identity)
        try:
            if action=='read':
                if present(directory,'cancel',0) or present(directory,'response.json',LIMIT_RESPONSE):raise FileNotFoundError()
                fd=os.open('request.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
                try:
                    before=os.fstat(fd);private(before)
                    if not 0<before.st_size<=LIMIT_REQUEST:raise ValueError()
                    with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(LIMIT_REQUEST+1)
                    after=os.fstat(fd);private(after)
                    if len(raw)!=before.st_size or signature(before)!=signature(after):raise ValueError()
                    current=os.fstat(directory)
                    token={'job':[current.st_dev,current.st_ino],'request':signature(after)}
                    if not active(parent,directory,identity,token):raise FileNotFoundError()
                    sys.stdout.buffer.write(json.dumps(token,separators=(',',':')).encode()+b'\n'+raw)
                finally:os.close(fd)
                return
            if action!='publish':raise ValueError()
            options=json.loads(sys.argv[3]);token=options['token'];size=options['size']
            if type(size) is not int or not 0<size<=LIMIT_RESPONSE:raise ValueError()
            raw=sys.stdin.buffer.read(LIMIT_RESPONSE+1)
            if len(raw)!=size:raise ValueError()
            if not active(parent,directory,identity,token):raise FileNotFoundError()
            temporary='.response-'+identity
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            try:
                with os.fdopen(fd,'wb',closefd=False) as stream:stream.write(raw);stream.flush()
                private(os.fstat(fd))
                if not active(parent,directory,identity,token):raise FileNotFoundError()
                os.rename(temporary,'response.json',src_dir_fd=directory,dst_dir_fd=directory)
                # Revoke a result if cancellation/directory removal raced rename.
                if not linked(parent,directory,identity) or present(directory,'cancel',0):
                    os.unlink('response.json',dir_fd=directory);raise FileNotFoundError()
                sys.stdout.write('published')
            finally:
                os.close(fd)
                try:os.unlink(temporary,dir_fd=directory)
                except FileNotFoundError:pass
        finally:os.close(directory)
    finally:os.close(parent)

try:main()
except FileNotFoundError:raise SystemExit(3)
except BaseException:raise SystemExit(2)
"""


class HostError(RuntimeError):
    """Stable safe codes only; never source bytes or raw provider errors."""


def clean_environment(environment=None):
    values = os.environ if environment is None else environment
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "PATHEXT"}
    return {key: value for key, value in values.items() if key.upper() in allowed}


class Executor:
    def __init__(self, container_id: str, run_id: str, report_dir: Path, *, stop_file=None):
        if not _CONTAINER.fullmatch(container_id) or not _ID.fullmatch(run_id):
            raise HostError("WORD_EXECUTION_PEER_INVALID")
        if report_dir.is_symlink() or not report_dir.is_dir():
            raise HostError("WORD_EXECUTION_REPORT_INVALID")
        self.container_id, self.run_id, self.report_dir = container_id, run_id, report_dir
        self.stop_file = stop_file
        self.docker_config = report_dir / ".docker"
        self.docker_config.mkdir(mode=0o700)
        self.receipts = report_dir / "receipts"
        self.receipts.mkdir(mode=0o700)
        self.records = report_dir / "requests"
        self.records.mkdir(mode=0o700)
        self.cleanup_closed = True
        self.processed = 0
        self.handled: set[str] = set()

    def _invoke(self, arguments, *, data=None, timeout=60):
        daemon = (
            "npipe:////./pipe/dockerDesktopLinuxEngine"
            if os.name == "nt"
            else "unix:///var/run/docker.sock"
        )
        environment = clean_environment()
        environment["DOCKER_CONFIG"] = str(self.docker_config)
        return subprocess.run(  # nosec B603 - fixed local daemon, no shell
            ["docker", "--host", daemon, *arguments],
            input=data,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=environment,
        )

    def inspect_peer(self):
        result = self._invoke(["container", "inspect", "--format", _INSPECT, self.container_id])
        try:
            value = json.loads(result.stdout)
            labels = value["Labels"] or {}
            mounts = value["Mounts"]
            tmpfs = value["Tmpfs"] or {}
            if (
                result.returncode
                or value["Id"] != self.container_id
                or value["Running"] is not True
                or value["User"] != "65532:65532"
                or value["Privileged"] is not False
                or value["ReadonlyRootfs"] is not True
                or labels.get("tender.test.run") != self.run_id
                or labels.get("tender.test.disposable") != "true"
                or not isinstance(mounts, list)
                or not isinstance(tmpfs, dict)
                or not (
                    isinstance(tmpfs.get("/tmp"), str)  # nosec B108 - exact Docker Desktop tmpfs key
                    or any(
                        item.get("Type") == "tmpfs" and item.get("Destination") == "/tmp"  # nosec B108 - exact required tmpfs identity
                        for item in mounts
                    )
                )
                or any(str(path).startswith("/tmp/") for path in tmpfs)  # nosec B108 - reject shadow mounts
                or any(
                    re.search(r"\.sock(?:$|[/\\])|docker_engine", str(item.get(key, "")), re.I)
                    for item in mounts
                    for key in ("Source", "Destination")
                )
                or any(
                    str(item.get("Destination", "")).startswith("/tmp/")  # nosec B108 - reject shadow mounts under fixed tmpfs
                    or item.get("Type") == "npipe"
                    or (
                        item.get("Type") == "bind"
                        and re.match(r"^/(?:var/)?run(?:/|$)", str(item.get("Source", "")))
                    )
                    for item in mounts
                )
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HostError("WORD_EXECUTION_PEER_INVALID") from None
        # Docker Desktop may omit tmpfs from Mounts. Its declaration alone is
        # insufficient: the fixed peer program verifies actual mountinfo too.
        try:
            if self._mailbox("verify") != b"verified":
                raise HostError("WORD_EXECUTION_PEER_TMPFS_INVALID")
        except HostError:
            raise HostError("WORD_EXECUTION_PEER_TMPFS_INVALID") from None

    def _mailbox(self, action, identity=None, *, token=None, data=None):
        if action not in {"verify", "list", "read", "publish"} or (
            identity is not None and not _ID.fullmatch(identity)
        ):
            raise HostError("WORD_EXECUTION_PATH_INVALID")
        arguments = [
            "exec",
            "--user",
            "65532:65532",
            "-i",
            self.container_id,
            "/usr/bin/env",
            "-i",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "LANG=C.UTF-8",
            "/usr/local/bin/python",
            "-I",
            "-c",
            HELPER,
            action,
        ]
        if identity is not None:
            arguments.append(identity)
        if action == "publish":
            arguments.append(json.dumps({"token": token, "size": len(data)}, separators=(",", ":")))
        result = self._invoke(arguments, data=data)
        if result.returncode == 3:
            return None
        if result.returncode:
            raise HostError("WORD_EXECUTION_MAILBOX_INVALID")
        return result.stdout

    def _dispatch(self, request, receipt):
        from packages.word_intake import prebuilt

        args = request["arguments"]
        return prebuilt.convert_in_isolation(
            args["source"], args["requested_fonts"], receipt=receipt
        )

    def stopped(self):
        return self.stop_file is not None and self.stop_file.exists()

    def cleanup(self):
        from deploy.word_prebuilt_acceptance import recover_conversion_receipts

        closed = True
        for directory in self.receipts.iterdir():
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or not _ID.fullmatch(directory.name)
            ):
                closed = False
                continue
            try:
                recovered = recover_conversion_receipts(directory)
                closed = closed and all(item["closed"] for item in recovered)
            except Exception:
                closed = False
        self.cleanup_closed = closed
        return closed

    def poll_once(self):
        from deploy.word_prebuilt_acceptance import recover_conversion_receipts, write_private
        from packages.word_intake import execution_transport as codec

        self.inspect_peer()
        candidate = self._mailbox("list")
        if candidate is None:
            return None
        try:
            identity = json.loads(candidate)
        except (ValueError, TypeError):
            raise HostError("WORD_EXECUTION_MAILBOX_INVALID") from None
        if identity is None:
            return None
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise HostError("WORD_EXECUTION_PATH_INVALID")
        if identity in self.handled:
            return None
        self.handled.add(identity)
        frozen = self._mailbox("read", identity)
        if frozen is None:
            return {"request_id": identity, "state": "discarded", "cleanup_closed": True}
        header, separator, raw = frozen.partition(b"\n")
        if not separator or len(header) > 2048 or not 0 < len(raw) <= codec.MAX_REQUEST_BYTES:
            raise HostError("WORD_EXECUTION_MAILBOX_INVALID")
        try:
            token = json.loads(header)
            request = codec.decode_request(raw)
            if request["request_id"] != identity:
                raise ValueError()
        except Exception:
            raise HostError("WORD_EXECUTION_REQUEST_INVALID") from None
        directory = self.receipts / identity
        directory.mkdir(mode=0o700)
        receipt = directory / f"conversion-{identity}.json"
        metadata = {
            "request_id": identity,
            "operation": request["operation"],
            "request_bytes": len(raw),
        }
        result = None
        interrupted = False
        previously_closed = self.cleanup_closed
        self.cleanup_closed = False
        try:
            result = self._dispatch(request, receipt)
        except KeyboardInterrupt:
            interrupted = True
            metadata["state"] = "cancelled"
            raise
        except Exception:
            metadata["error_code"] = "WORD_EXECUTION_HOST_RENDER_FAILED"
        finally:
            recovered = recover_conversion_receipts(directory)
            closed = all(item["closed"] for item in recovered)
            if (
                isinstance(result, dict)
                and result.get("error_code") is None
                and result.get("pdf") is not None
                and (not recovered or result.get("cleanup_confirmed") is not True)
            ):
                closed = False
            self.cleanup_closed = previously_closed and closed
            metadata["cleanup_closed"] = closed
            metadata["conversion_receipts"] = recovered
            if interrupted:
                write_private(
                    self.records / f"{identity}.json", json.dumps(metadata, indent=2) + "\n"
                )
        if not metadata["cleanup_closed"]:
            response = codec.encode_error(raw, "WORD_EXECUTION_CLEANUP_UNCONFIRMED")
        elif result is None:
            response = codec.encode_error(raw, "WORD_EXECUTION_HOST_RENDER_FAILED")
        else:
            result["resource_receipt"] = {
                "closed": True,
                "receipts": metadata["conversion_receipts"],
            }
            try:
                response = codec.encode_response(raw, result)
            except Exception:
                response = codec.encode_error(raw, "WORD_EXECUTION_RESULT_INVALID")
        if self.stopped():
            metadata["state"] = "discarded"
        else:
            self.inspect_peer()
            published = self._mailbox("publish", identity, token=token, data=response)
            metadata["state"] = "published" if published == b"published" else "discarded"
        self.processed += 1
        write_private(self.records / f"{identity}.json", json.dumps(metadata, indent=2) + "\n")
        return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=0.1)
    args = parser.parse_args(argv)
    if (
        not _CONTAINER.fullmatch(args.container_id)
        or not _ID.fullmatch(args.run_id)
        or not 0.05 <= args.poll_seconds <= 5
    ):
        print(
            json.dumps({"state": "failed", "error_code": "WORD_EXECUTION_OPERATOR_CONFIG_INVALID"})
        )
        return 2
    directory = args.report_dir.resolve()
    try:
        directory.mkdir(mode=0o700)
    except OSError:
        print(json.dumps({"state": "failed", "error_code": "WORD_EXECUTION_REPORT_INVALID"}))
        return 2
    # CLI only: imports in tests do not mutate their process environment.
    environment = clean_environment()
    environment["WORKBENCH_ENV_FILE"] = str(directory / "explicit-nonexistent-business.env")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.clear()
    os.environ.update(environment)
    mask = os.umask(0o077)
    sys.path[:] = [str(ROOT), *(item for item in sys.path if item != str(ROOT))]
    from deploy.word_prebuilt_acceptance import private_directory, write_private

    executor = None
    status = {"state": "stopped", "cleanup_closed": True}
    try:
        private_directory(directory)
        executor = Executor(args.container_id, args.run_id, directory, stop_file=args.stop_file)
        executor.inspect_peer()
        write_private(
            directory / "ready.json",
            json.dumps({"state": "ready", "container_id": args.container_id, "run_id": args.run_id})
            + "\n",
        )
        while not executor.stopped():
            executor.poll_once()
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        status["state"] = "cancelled"
    except Exception as error:
        status["state"] = "failed"
        status["error_code"] = (
            str(error) if isinstance(error, HostError) else "WORD_EXECUTION_HOST_FAILED"
        )
    finally:
        if executor is not None:
            try:
                executor.cleanup()
            except Exception:
                executor.cleanup_closed = False
            status.update(cleanup_closed=executor.cleanup_closed, processed=executor.processed)
        write_private(directory / "summary.json", json.dumps(status, indent=2) + "\n")
        print(json.dumps(status))
        os.umask(mask)
    return 0 if status["state"] == "stopped" and status["cleanup_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

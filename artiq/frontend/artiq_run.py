#!/usr/bin/env python3.5
# Copyright (C) 2014, 2015 M-Labs Limited
# Copyright (C) 2014, 2015 Robert Jordens <jordens@gmail.com>

import argparse
import sys
from operator import itemgetter
import logging
from collections import defaultdict

import h5py

from llvmlite_artiq import binding as llvm

from artiq.language.environment import EnvExperiment
from artiq.master.databases import DeviceDB, DatasetDB
from artiq.master.worker_db import DeviceManager, DatasetManager
from artiq.coredevice.core import CompileError, host_only
from artiq.compiler.embedding import ObjectMap
from artiq.compiler.targets import OR1KTarget
from artiq.tools import *

logger = logging.getLogger(__name__)

class StubObject:
    def __setattr__(self, name, value):
        pass

class StubObjectMap:
    def __init__(self):
        stub_object = StubObject()
        self.forward_map = defaultdict(lambda: stub_object)
        self.forward_map[1] = lambda _: None # return RPC
        self.next_id = -1

    def retrieve(self, object_id):
        return self.forward_map[object_id]

    def store(self, value):
        self.forward_map[self.next_id] = value
        self.next_id -= 1

class FileRunner(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_argument("file")
        self.target = OR1KTarget()

    def run(self):
        kernel_library = self.compile()

        self.core.comm.load(kernel_library)
        self.core.comm.run()
        self.core.comm.serve(StubObjectMap(),
            lambda addresses: self.target.symbolize(kernel_library, addresses))

class ELFRunner(FileRunner):
    def compile(self):
        with open(self.file, "rb") as f:
            return f.read()

class LLVMIRRunner(FileRunner):
    def compile(self):
        with open(self.file, "r") as f:
            llmodule = llvm.parse_assembly(f.read())
        llmodule.verify()
        return self.target.link([self.target.assemble(llmodule)],
                                init_fn='__modinit__')

class LLVMBitcodeRunner(FileRunner):
    def compile(self):
        with open(self.file, "rb") as f:
            llmodule = llvm.parse_bitcode(f.read())
        llmodule.verify()
        return self.target.link([self.target.assemble(llmodule)],
                                init_fn='__modinit__')


class DummyScheduler:
    def __init__(self):
        self.rid = 0
        self.pipeline_name = "main"
        self.priority = 0
        self.expid = None

        self._next_rid = 1

    def submit(self, pipeline_name, expid, priority, due_date, flush):
        rid = self._next_rid
        self._next_rid += 1
        logger.info("Submitting: %s, RID=%s", expid, rid)
        return rid

    def delete(self, rid):
        logger.info("Deleting RID %s", rid)

    def request_termination(self, rid):
        logger.info("Requesting termination of RID %s", rid)

    def get_status(self):
        return dict()

    @host_only
    def pause(self):
        pass


def get_argparser(with_file=True):
    parser = argparse.ArgumentParser(
        description="Local experiment running tool")

    verbosity_args(parser)
    parser.add_argument("--device-db", default="device_db.pyon",
                        help="device database file (default: '%(default)s')")
    parser.add_argument("--dataset-db", default="dataset_db.pyon",
                        help="dataset file (default: '%(default)s')")

    parser.add_argument("-e", "--experiment", default=None,
                        help="experiment to run")
    parser.add_argument("-o", "--hdf5", default=None,
                        help="write results to specified HDF5 file"
                             " (default: print them)")
    if with_file:
        parser.add_argument("file",
                            help="file containing the experiment to run")
    parser.add_argument("arguments", nargs="*",
                        help="run arguments")

    return parser


def _build_experiment(device_mgr, dataset_mgr, args):
    if hasattr(args, "file"):
        is_elf = args.file.endswith(".elf")
        is_ll  = args.file.endswith(".ll")
        is_bc  = args.file.endswith(".bc")
        if is_elf or is_ll or is_bc:
            if args.arguments:
                raise ValueError("arguments not supported for precompiled kernels")
            if args.experiment:
                raise ValueError("experiment-by-name not supported "
                                 "for precompiled kernels")
        if is_elf:
            return ELFRunner(device_mgr, dataset_mgr, file=args.file)
        elif is_ll:
            return LLVMIRRunner(device_mgr, dataset_mgr, file=args.file)
        elif is_bc:
            return LLVMBitcodeRunner(device_mgr, dataset_mgr, file=args.file)
        else:
            module = file_import(args.file, prefix="artiq_run_")
        file = args.file
    else:
        module = sys.modules["__main__"]
        file = getattr(module, "__file__")
    exp = get_experiment(module, args.experiment)
    arguments = parse_arguments(args.arguments)
    expid = {
        "file": file,
        "experiment": args.experiment,
        "arguments": arguments
    }
    device_mgr.virtual_devices["scheduler"].expid = expid
    return exp(device_mgr, dataset_mgr, **arguments)


def run(with_file=False):
    args = get_argparser(with_file).parse_args()
    init_logger(args)

    device_mgr = DeviceManager(DeviceDB(args.device_db),
                               virtual_devices={"scheduler": DummyScheduler()})
    dataset_db = DatasetDB(args.dataset_db)
    dataset_mgr = DatasetManager(dataset_db)

    try:
        exp_inst = _build_experiment(device_mgr, dataset_mgr, args)
        exp_inst.prepare()
        exp_inst.run()
        exp_inst.analyze()
    except CompileError as error:
        return
    except Exception as exn:
        if hasattr(exn, "artiq_core_exception"):
            print(exn.artiq_core_exception, file=sys.stderr)
        raise exn
    finally:
        device_mgr.close_devices()

    if args.hdf5 is not None:
        with h5py.File(args.hdf5, "w") as f:
            dataset_mgr.write_hdf5(f)
    else:
        for k, v in sorted(dataset_mgr.local.items(), key=itemgetter(0)):
            print("{}: {}".format(k, v))
    dataset_db.save()


def main():
    return run(with_file=True)


if __name__ == "__main__":
    main()

import os
import torch
import importlib
import torch.nn as nn

from config import system_configs
from models.py_utils.data_parallel import DataParallel

torch.manual_seed(317)


def print_log(text, system_configs):
    # print("[print_log] text", text,"system_configs.snapshot_file",
    #  system_configs.snapshot_file)
    path = "_".join(system_configs.snapshot_file.split("_")[:-1])
    # print("[print_log] path", path)
    path_file_log = path +"_log.txt"
    with open(path_file_log, 'a') as f:
        f.write(text+'\n')

class Network(nn.Module):
    def __init__(self, model, loss):
        super(Network, self).__init__()

        self.model = model
        self.loss  = loss

    def forward(self, xs, ys, **kwargs):
        preds = self.model(*xs, **kwargs)
        # print("[Network forward] preds", len(preds), "ys", len(ys))
        # [Network forward] preds 18 ys 10
        loss  = self.loss(preds, ys, **kwargs)
        return loss

# for model backward compatibility
# previously model was wrapped by DataParallel module
class DummyModule(nn.Module):
    def __init__(self, model):
        super(DummyModule, self).__init__()
        self.module = model

    def forward(self, *xs, **kwargs):
        return self.module(*xs, **kwargs)

class NetworkFactory(object):
    def __init__(self, db, cuda_flag):
        super(NetworkFactory, self).__init__()
        # print("[NetworkFactory __init__] db", db )
        module_file = "models.{}".format(system_configs.snapshot_name)
        # print("[NetworkFactory __init__] module_file: {}".format(module_file))
        # [NetworkFactory __init__] module_file: models.medical_ExtremeNet
        nnet_module = importlib.import_module(module_file)
        # print("[NetworkFactory __init__] nnet_module", nnet_module)
        self.model   = DummyModule(nnet_module.model(db))
        self.loss    = nnet_module.loss # yezheng: this is last line in models/ExtremeNet.py
        self.network = Network(self.model, self.loss)
        self.cuda_flag = cuda_flag
        if self.cuda_flag:
            self.network = DataParallel(self.network, chunk_sizes=system_configs.chunk_sizes)
        

        total_params = 0
        for params in self.model.parameters():
            num_params = 1
            for x in params.size():
                num_params *= x
            total_params += num_params
        print("total parameters: {}".format(total_params))

        if system_configs.opt_algo == "adam":
            self.optimizer = torch.optim.Adam(
                filter(lambda p: p.requires_grad, self.model.parameters())
            )
        elif system_configs.opt_algo == "sgd":
            self.optimizer = torch.optim.SGD(
                filter(lambda p: p.requires_grad, self.model.parameters()),
                lr=system_configs.learning_rate, 
                momentum=0.9, weight_decay=0.0001
            )
        else:
            raise ValueError("unknown optimizer")

        # print("[NetworkFactory] system_configs.snapshot_file",system_configs.snapshot_file)
        # ystem_configs.snapshot_file ./cache/nnet/medical_ExtremeNet/medical_ExtremeNet_{}.pkl

    def cuda(self):
        self.model.cuda()

    def train_mode(self):
        self.network.train()

    def eval_mode(self):
        self.network.eval()

    def train(self, xs, ys, **kwargs):
        if torch.cuda.is_available() and self.cuda_flag:
            xs = [x.cuda(non_blocking=True) for x in xs]
            ys = [y.cuda(non_blocking=True) for y in ys]

        self.optimizer.zero_grad()
        loss = self.network(xs, ys)
        loss = loss.mean()
        loss.backward()
        self.optimizer.step()
        return loss

    def validate(self, xs, ys, **kwargs):
        with torch.no_grad():
            if torch.cuda.is_available() and self.cuda_flag:
                xs = [x.cuda(non_blocking=True) for x in xs]
                ys = [y.cuda(non_blocking=True) for y in ys]

            loss = self.network(xs, ys)
            loss = loss.mean()
            return loss

    def test(self, xs, **kwargs):
        
        with torch.no_grad():
            if torch.cuda.is_available() and self.cuda_flag:
                xs = [x.cuda(non_blocking=True) for x in xs]
            # print("[NetworkFactory test] list len(xs)", len(xs),
            #     "xs[0]",xs[0].shape, type(xs[0]) )#, "self.model", self.model)
            return self.model(*xs, **kwargs)


    def set_lr(self, lr):
        print("setting learning rate to: {}".format(lr))
        for param_group in self.optimizer.param_groups:
            param_group["lr"] = lr

    def load_pretrained_params(self, pretrained_model):
        print("loading from {}".format(pretrained_model))
        with open(pretrained_model, "rb") as f:
            if torch.cuda.is_available() and self.cuda_flag:
                params = torch.load(f)
            else:
                params = torch.load(f, map_location = 'cpu')
            self.model.load_state_dict(params, strict=False)

    def load_params(self, iteration):
        cache_file = system_configs.snapshot_file.format(iteration)
        print("loading model from {}".format(cache_file))
        print_log("loading model from {}".format(cache_file), system_configs)
        with open(cache_file, "rb") as f:
            if torch.cuda.is_available()  and self.cuda_flag:
                params = torch.load(f)
            else:
                params = torch.load(f, map_location = 'cpu')
            self.model.load_state_dict(params)

    def save_params(self, iteration):
        cache_file = system_configs.snapshot_file.format(iteration)

        print("saving model to {}".format(cache_file))
        with open(cache_file, "wb") as f:
            params = self.model.state_dict()
            torch.save(params, f)

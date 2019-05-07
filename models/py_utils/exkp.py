#-----
#yezheng: this file (exkp.py) is in ExtremeNet but not CornerNet -- it inherits kp.py in CornerNet
import numpy as np
import torch
import torch.nn as nn

from .utils import convolution, residual
from .utils import make_layer, make_layer_revr

from .kp_utils import _tranpose_and_gather_feat, _exct_decode
from .kp_utils import _sigmoid, _regr_loss, _neg_loss
from .kp_utils import make_kp_layer, make_hg_layer #hg = hourglass
from .kp_utils import make_merge_layer, make_inter_layer, make_cnv_layer
from .kp_utils import _h_aggregate, _v_aggregate
from .kp_utils import _nms, _topk, _gather_feat
from utils.debugger import Debugger



class kp_module(nn.Module):
    def __init__(
        self, n, dims, modules, layer=residual,
        make_up_layer=make_layer, make_low_layer=make_layer,#make_hg_layer_revr=make_layer_revr, #make_pool_layer=make_pool_layer, make_unpool_layer=make_unpool_layer,
        make_merge_layer=make_merge_layer, **kwargs
    ):
        super(kp_module, self).__init__()

        self.n   = n
        curr_mod = modules[0]
        next_mod = modules[1]

        curr_dim = dims[0]
        next_dim = dims[1]
        # print("[exkp.py kp_module __init__] curr_dim", curr_dim, "next_dim", next_dim)

        self.up1  = make_up_layer(
            3, curr_dim, curr_dim, curr_mod, 
            layer=layer, **kwargs
        )  
        self.max1 = nn.Sequential()
        self.low1 = make_hg_layer(
            3, curr_dim, next_dim, curr_mod,
            layer=layer, **kwargs
        )
        self.low2 = None
        
        if self.n > 1:
            self.low2 = kp_module(
                n - 1, dims[1:], modules[1:], layer=layer, 
                make_up_layer=make_up_layer, 
                make_low_layer=make_low_layer,
                make_merge_layer=make_merge_layer,
                **kwargs
            )  

        else:
            self.low2 = make_low_layer(
                3, next_dim, next_dim, next_mod,
                layer=layer, **kwargs
            )

        self.low3 = make_layer_revr(
            3, next_dim, curr_dim, curr_mod,
            layer=layer, **kwargs
        )
        self.up2 = nn.Upsample(scale_factor=2)
        self.merge = make_merge_layer(curr_dim)

    def forward(self, x):
        exkp_flag = False

        if exkp_flag:print("[exkp.py kp_module forward] x", x.shape)
        if exkp_flag:print("[exkp.py kp_module forward]----------")
        up1  = self.up1(x)
        if exkp_flag:print("[exkp.py kp_module forward] up1", up1.shape)
        max1 = self.max1(x)
        if exkp_flag:print("[exkp.py kp_module forward] max1", max1.shape)
        low1 = self.low1(max1)
        if exkp_flag:print("[exkp.py kp_module forward] low1", low1.shape)
        low2 = self.low2(low1)
        if exkp_flag:print("[exkp.py kp_module forward] low2", low2.shape)
        low3 = self.low3(low2)
        up2  = self.up2(low3)
        ret =self.merge(up1, up2)
        if exkp_flag:print("[exkp.py kp_module forward] ret", ret.shape)
        if exkp_flag:print("[exkp.py kp_module forward]==========")
        return ret

class exkp(nn.Module):
    def __init__(
        self, n, nstack, dims, modules, out_dim, pre=None, cnv_dim=256, 
        make_tl_layer=None, make_br_layer=None,
        make_cnv_layer=make_cnv_layer, make_heat_layer=make_kp_layer,
        make_tag_layer=make_kp_layer, make_regr_layer=make_kp_layer,
        make_up_layer=make_layer, make_low_layer=make_layer, 
        make_merge_layer=make_merge_layer, make_inter_layer=make_inter_layer, 
        kp_layer=residual
    ):
        super(exkp, self).__init__()

        self.nstack    = nstack
        self._decode   = _exct_decode

        curr_dim = dims[0]

        self.pre = nn.Sequential(
            convolution(7, 3, 128, stride=2),
            residual(3, 128, 256, stride=2)
        ) if pre is None else pre

        self.kps  = nn.ModuleList([
            kp_module(
                n, dims, modules, layer=kp_layer,
                make_up_layer=make_up_layer,
                make_low_layer=make_low_layer,
                make_merge_layer=make_merge_layer
            ) for _ in range(nstack)
        ])
        self.cnvs = nn.ModuleList([
            make_cnv_layer(curr_dim, cnv_dim) for _ in range(nstack)
        ])

        ## keypoint heatmaps
        self.t_heats = nn.ModuleList([
            make_heat_layer(cnv_dim, curr_dim, out_dim) for _ in range(nstack)
        ])

        self.l_heats = nn.ModuleList([
            make_heat_layer(cnv_dim, curr_dim, out_dim) for _ in range(nstack)
        ])

        self.b_heats = nn.ModuleList([
            make_heat_layer(cnv_dim, curr_dim, out_dim) for _ in range(nstack)
        ])

        self.r_heats = nn.ModuleList([
            make_heat_layer(cnv_dim, curr_dim, out_dim) for _ in range(nstack)
        ])

        self.ct_heats = nn.ModuleList([
            make_heat_layer(cnv_dim, curr_dim, out_dim) for _ in range(nstack)
        ])

        for t_heat, l_heat, b_heat, r_heat, ct_heat in \
          zip(self.t_heats, self.l_heats, self.b_heats, \
              self.r_heats, self.ct_heats):
            t_heat[-1].bias.data.fill_(-2.19)
            l_heat[-1].bias.data.fill_(-2.19)
            b_heat[-1].bias.data.fill_(-2.19)
            r_heat[-1].bias.data.fill_(-2.19)
            ct_heat[-1].bias.data.fill_(-2.19)

        self.inters = nn.ModuleList([
            make_inter_layer(curr_dim) for _ in range(nstack - 1)
        ])

        self.inters_ = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(curr_dim, curr_dim, (1, 1), bias=False),
                nn.BatchNorm2d(curr_dim)
            ) for _ in range(nstack - 1)
        ])
        self.cnvs_   = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(cnv_dim, curr_dim, (1, 1), bias=False),
                nn.BatchNorm2d(curr_dim)
            ) for _ in range(nstack - 1)
        ])

        self.t_regrs = nn.ModuleList([
            make_regr_layer(cnv_dim, curr_dim, 2) for _ in range(nstack)
        ])
        self.l_regrs = nn.ModuleList([
            make_regr_layer(cnv_dim, curr_dim, 2) for _ in range(nstack)
        ])
        self.b_regrs = nn.ModuleList([
            make_regr_layer(cnv_dim, curr_dim, 2) for _ in range(nstack)
        ])
        self.r_regrs = nn.ModuleList([
            make_regr_layer(cnv_dim, curr_dim, 2) for _ in range(nstack)
        ])

        self.relu = nn.ReLU(inplace=True)

    # def _train(self, *xs):
    #     # print("[exkp.py exkp _train] xs", len(xs))
    #     image  = xs[0]

    #     inter = self.pre(image)
    #     outs  = []
    #     layers = zip(
    #         self.kps, self.cnvs,
    #         self.t_heats, self.l_heats, self.b_heats, self.r_heats,
    #         self.ct_heats,
    #         self.t_regrs, self.l_regrs, self.b_regrs, self.r_regrs,
    #     )
    #     t_inds = xs[1] #t_tags in medical_extreme.py kp_detection(
    #     l_inds = xs[2] 
    #     b_inds = xs[3]
    #     r_inds = xs[4]
    #     for ind, layer in enumerate(layers):
    #         kp_, cnv_          = layer[0:2]
    #         t_heat_, l_heat_, b_heat_, r_heat_ = layer[2:6]
    #         ct_heat_                           = layer[6]
    #         t_regr_, l_regr_, b_regr_, r_regr_ = layer[7:11]

    #         kp  = kp_(inter)
    #         cnv = cnv_(kp)

    #         t_heat, l_heat = t_heat_(cnv), l_heat_(cnv)
    #         b_heat, r_heat = b_heat_(cnv), r_heat_(cnv)
    #         ct_heat        = ct_heat_(cnv)

    #         t_regr, l_regr = t_regr_(cnv), l_regr_(cnv)
    #         b_regr, r_regr = b_regr_(cnv), r_regr_(cnv)

    #         t_regr = _tranpose_and_gather_feat(t_regr, t_inds)
    #         l_regr = _tranpose_and_gather_feat(l_regr, l_inds)
    #         b_regr = _tranpose_and_gather_feat(b_regr, b_inds)
    #         r_regr = _tranpose_and_gather_feat(r_regr, r_inds)

    #         outs += [t_heat, l_heat, b_heat, r_heat, ct_heat, \
    #                  t_regr, l_regr, b_regr, r_regr]

    #         if ind < self.nstack - 1:
    #             inter = self.inters_[ind](inter) + self.cnvs_[ind](cnv)
    #             inter = self.relu(inter)
    #             inter = self.inters[ind](inter)
    #     # print("[exkp.py exkp _train] outs", len(outs),type(outs)) # 18 <class 'list'>
    #     return outs

    # def _test(self, *xs, **kwargs):
    #     print("[exkp.py exkp _test] xs", len(xs))
    #     for x in xs:
    #         print("[exkp.py exkp _test] x", x.shape)

    #     image = xs[0]

    #     inter = self.pre(image)
    #     outs  = []

    #     layers = zip(
    #         self.kps, self.cnvs,
    #         self.t_heats, self.l_heats, self.b_heats, self.r_heats,
    #         self.ct_heats,
    #         self.t_regrs, self.l_regrs, self.b_regrs, self.r_regrs,
    #     )
    #     for ind, layer in enumerate(layers):
    #         kp_, cnv_                          = layer[0:2]
    #         t_heat_, l_heat_, b_heat_, r_heat_ = layer[2:6]
    #         ct_heat_                           = layer[6]
    #         t_regr_, l_regr_, b_regr_, r_regr_ = layer[7:11]

    #         kp  = kp_(inter)
    #         cnv = cnv_(kp)

    #         if ind == self.nstack - 1:
    #             t_heat, l_heat = t_heat_(cnv), l_heat_(cnv)
    #             b_heat, r_heat = b_heat_(cnv), r_heat_(cnv)
    #             ct_heat        = ct_heat_(cnv)

    #             t_regr, l_regr = t_regr_(cnv), l_regr_(cnv)
    #             b_regr, r_regr = b_regr_(cnv), r_regr_(cnv)

    #             outs += [t_heat, l_heat, b_heat, r_heat, ct_heat,
    #                      t_regr, l_regr, b_regr, r_regr]

    #         if ind < self.nstack - 1:
    #             inter = self.inters_[ind](inter) + self.cnvs_[ind](cnv)
    #             inter = self.relu(inter)
    #             inter = self.inters[ind](inter)
    #     print("[exkp.py exkp _test] kwargs['debug']", kwargs['debug'], "kwargs", kwargs)
    #     if kwargs['debug']:
    #         _debug(image, t_heat, l_heat, b_heat, r_heat, ct_heat)
    #     del kwargs['debug']
    #     return self._decode(*outs[-9:], **kwargs)

    # def forward(self, *xs, **kwargs):
    #     if len(xs) > 1:
    #         return self._train(*xs, **kwargs)
    #     return self._test(*xs, **kwargs)

    def forward(self, *xs, **kwargs):
        # print("[exkp.py exkp forward] xs", len(xs))
        image  = xs[0]

        inter = self.pre(image)
        outs  = []
        layers = zip(
            self.kps, self.cnvs,
            self.t_heats, self.l_heats, self.b_heats, self.r_heats,
            self.ct_heats,
            self.t_regrs, self.l_regrs, self.b_regrs, self.r_regrs,
        )
        if len(xs)>1: #yezheng: training procedure
            t_inds = xs[1] #t_tags in medical_extreme.py kp_detection(
            l_inds = xs[2] 
            b_inds = xs[3]
            r_inds = xs[4]
        for ind, layer in enumerate(layers):
            kp_, cnv_          = layer[0:2]
            t_heat_, l_heat_, b_heat_, r_heat_ = layer[2:6]
            ct_heat_                           = layer[6]
            t_regr_, l_regr_, b_regr_, r_regr_ = layer[7:11]

            kp  = kp_(inter)
            cnv = cnv_(kp)

            if len(xs)>1 or ind == self.nstack - 1: 
            #yezheng: training procedure or last layer of testing procedure
                t_heat, l_heat = t_heat_(cnv), l_heat_(cnv)
                b_heat, r_heat = b_heat_(cnv), r_heat_(cnv)
                ct_heat        = ct_heat_(cnv)

                t_regr, l_regr = t_regr_(cnv), l_regr_(cnv)
                b_regr, r_regr = b_regr_(cnv), r_regr_(cnv)

                if len(xs)>1: #yezheng: training procedure
                    t_regr = _tranpose_and_gather_feat(t_regr, t_inds)
                    l_regr = _tranpose_and_gather_feat(l_regr, l_inds)
                    b_regr = _tranpose_and_gather_feat(b_regr, b_inds)
                    r_regr = _tranpose_and_gather_feat(r_regr, r_inds)
                outs += [t_heat, l_heat, b_heat, r_heat, ct_heat, 
                     t_regr, l_regr, b_regr, r_regr]

            
            if ind < self.nstack - 1:
                inter = self.inters_[ind](inter) + self.cnvs_[ind](cnv)
                inter = self.relu(inter)
                inter = self.inters[ind](inter)
            
        if len(xs)>1: #yezheng: training procedure
            return outs
        else: #yezheng: testing procedure
            if kwargs['debug']:
                _debug(image, t_heat, l_heat, b_heat, r_heat, ct_heat)
            del kwargs['debug']
            #yezheng: _exct_decode( from kp_utils.py
            K=40
            kernel=3
            aggr_weight=0.1
            scores_thresh=0.1
            center_thresh=0.1
            num_dets=1000
            batch, cat, height, width = t_heat.size()
    
            ''' 
            filter_kernel = 0.1
            t_heat = _filter(t_heat, direction='h', val=filter_kernel)
            l_heat = _filter(l_heat, direction='v', val=filter_kernel)
            b_heat = _filter(b_heat, direction='h', val=filter_kernel)
            r_heat = _filter(r_heat, direction='v', val=filter_kernel)
            '''
            
            t_heat = torch.sigmoid(t_heat)
            l_heat = torch.sigmoid(l_heat)
            b_heat = torch.sigmoid(b_heat)
            r_heat = torch.sigmoid(r_heat)
            ct_heat = torch.sigmoid(ct_heat)
            # print("[kp_utils.py _exct_decode] aggr_weight", aggr_weight)
            # kp_utils.py _exct_decode] aggr_weight 0.1
            # #---------
            # #yezheng: comment out
            # # aggr_weight = 0 # yezheng: this is not important
            # if aggr_weight > 0:
            #     t_heat = _h_aggregate(t_heat, aggr_weight=aggr_weight)
            #     l_heat = _v_aggregate(l_heat, aggr_weight=aggr_weight)
            #     b_heat = _h_aggregate(b_heat, aggr_weight=aggr_weight)
            #     r_heat = _v_aggregate(r_heat, aggr_weight=aggr_weight)
            # #---------
            # print("[kp_utils.py _exct_decode] kernel", kernel)
            # [kp_utils.py _exct_decode] kernel 3
            # perform nms on heatmaps
            t_heat = _nms(t_heat, kernel=kernel)
            l_heat = _nms(l_heat, kernel=kernel)
            b_heat = _nms(b_heat, kernel=kernel)
            r_heat = _nms(r_heat, kernel=kernel)
            # #---------
            # #yezheng: comment out
            # t_heat[t_heat > 1] = 1
            # l_heat[l_heat > 1] = 1
            # b_heat[b_heat > 1] = 1
            # r_heat[r_heat > 1] = 1
            # #---------
            # yezheng: what does this K mean?
            t_scores, t_inds, t_clses, t_ys, t_xs = _topk(t_heat, K=K)
            l_scores, l_inds, l_clses, l_ys, l_xs = _topk(l_heat, K=K)
            b_scores, b_inds, b_clses, b_ys, b_xs = _topk(b_heat, K=K)
            r_scores, r_inds, r_clses, r_ys, r_xs = _topk(r_heat, K=K)
            #yezheng: these ares just creating meshes
            t_ys = t_ys.view(batch, K, 1, 1, 1).expand(batch, K, K, K, K)
            t_xs = t_xs.view(batch, K, 1, 1, 1).expand(batch, K, K, K, K)
            l_ys = l_ys.view(batch, 1, K, 1, 1).expand(batch, K, K, K, K)
            l_xs = l_xs.view(batch, 1, K, 1, 1).expand(batch, K, K, K, K)
            b_ys = b_ys.view(batch, 1, 1, K, 1).expand(batch, K, K, K, K)
            b_xs = b_xs.view(batch, 1, 1, K, 1).expand(batch, K, K, K, K)
            r_ys = r_ys.view(batch, 1, 1, 1, K).expand(batch, K, K, K, K)
            r_xs = r_xs.view(batch, 1, 1, 1, K).expand(batch, K, K, K, K)

            t_clses = t_clses.view(batch, K, 1, 1, 1).expand(batch, K, K, K, K)
            l_clses = l_clses.view(batch, 1, K, 1, 1).expand(batch, K, K, K, K)
            b_clses = b_clses.view(batch, 1, 1, K, 1).expand(batch, K, K, K, K)
            r_clses = r_clses.view(batch, 1, 1, 1, K).expand(batch, K, K, K, K)
            box_ct_xs = ((l_xs + r_xs + 0.5) / 2).long()
            box_ct_ys = ((t_ys + b_ys + 0.5) / 2).long()
            ct_inds = t_clses.long() * (height * width) + box_ct_ys * width + box_ct_xs
            ct_inds = ct_inds.view(batch, -1)
            ct_heat = ct_heat.view(batch, -1, 1)
            ct_scores = _gather_feat(ct_heat, ct_inds)

            t_scores = t_scores.view(batch, K, 1, 1, 1).expand(batch, K, K, K, K)
            l_scores = l_scores.view(batch, 1, K, 1, 1).expand(batch, K, K, K, K)
            b_scores = b_scores.view(batch, 1, 1, K, 1).expand(batch, K, K, K, K)
            r_scores = r_scores.view(batch, 1, 1, 1, K).expand(batch, K, K, K, K)
            ct_scores = ct_scores.view(batch, K, K, K, K)
            scores    = (t_scores + l_scores + b_scores + r_scores + 2 * ct_scores) / 6

            # reject boxes based on classes
            cls_inds = (t_clses != l_clses) + (t_clses != b_clses) + \
                       (t_clses != r_clses)
            cls_inds = (cls_inds > 0)

            top_inds  = (t_ys > l_ys) + (t_ys > b_ys) + (t_ys > r_ys)
            top_inds = (top_inds > 0)
            left_inds  = (l_xs > t_xs) + (l_xs > b_xs) + (l_xs > r_xs)
            left_inds = (left_inds > 0)
            bottom_inds  = (b_ys < t_ys) + (b_ys < l_ys) + (b_ys < r_ys)
            bottom_inds = (bottom_inds > 0)
            right_inds  = (r_xs < t_xs) + (r_xs < l_xs) + (r_xs < b_xs)
            right_inds = (right_inds > 0)

            sc_inds = (t_scores < scores_thresh) + (l_scores < scores_thresh) + \
                      (b_scores < scores_thresh) + (r_scores < scores_thresh) + \
                      (ct_scores < center_thresh)
            sc_inds = (sc_inds > 0)
            
            '''
            scores[sc_inds]   = -1
            scores[cls_inds]  = -1
            scores[top_inds]  = -1
            scores[left_inds] = -1
            scores[bottom_inds]  = -1
            scores[right_inds] = -1
            '''
            scores = scores - sc_inds.float()
            scores = scores - cls_inds.float()
            scores = scores - top_inds.float()
            scores = scores - left_inds.float()
            scores = scores - bottom_inds.float()
            scores = scores - right_inds.float()


            scores = scores.view(batch, -1)
            scores, inds = torch.topk(scores, num_dets)
            scores = scores.unsqueeze(2)

            t_regr = _tranpose_and_gather_feat(t_regr, t_inds)
            t_regr = t_regr.view(batch, K, 1, 1, 1, 2)
            l_regr = _tranpose_and_gather_feat(l_regr, l_inds)
            l_regr = l_regr.view(batch, 1, K, 1, 1, 2)
            b_regr = _tranpose_and_gather_feat(b_regr, b_inds)
            b_regr = b_regr.view(batch, 1, 1, K, 1, 2)
            r_regr = _tranpose_and_gather_feat(r_regr, r_inds)
            r_regr = r_regr.view(batch, 1, 1, 1, K, 2)

            t_xs = t_xs + t_regr[..., 0]
            t_ys = t_ys + t_regr[..., 1]
            l_xs = l_xs + l_regr[..., 0]
            l_ys = l_ys + l_regr[..., 1]
            b_xs = b_xs + b_regr[..., 0]
            b_ys = b_ys + b_regr[..., 1]
            r_xs = r_xs + r_regr[..., 0]
            r_ys = r_ys + r_regr[..., 1]
            
            bboxes = torch.stack((l_xs, t_ys, r_xs, b_ys), dim=5)
            bboxes = bboxes.view(batch, -1, 4)
            bboxes = _gather_feat(bboxes, inds)

            clses  = t_clses.contiguous().view(batch, -1, 1)
            clses  = _gather_feat(clses, inds).float()

            t_xs = t_xs.contiguous().view(batch, -1, 1)
            t_xs = _gather_feat(t_xs, inds).float() #yezheng: this function is fairly important _gather_feat
            t_ys = t_ys.contiguous().view(batch, -1, 1)
            t_ys = _gather_feat(t_ys, inds).float()
            l_xs = l_xs.contiguous().view(batch, -1, 1)
            l_xs = _gather_feat(l_xs, inds).float()
            l_ys = l_ys.contiguous().view(batch, -1, 1)
            l_ys = _gather_feat(l_ys, inds).float()
            b_xs = b_xs.contiguous().view(batch, -1, 1)
            b_xs = _gather_feat(b_xs, inds).float()
            b_ys = b_ys.contiguous().view(batch, -1, 1)
            b_ys = _gather_feat(b_ys, inds).float()
            r_xs = r_xs.contiguous().view(batch, -1, 1)
            r_xs = _gather_feat(r_xs, inds).float()
            r_ys = r_ys.contiguous().view(batch, -1, 1)
            r_ys = _gather_feat(r_ys, inds).float()


            detections = torch.cat([bboxes, scores, t_xs, t_ys, l_xs, l_ys, 
                                    b_xs, b_ys, r_xs, r_ys, clses], dim=2)
            print("[kp_utils.py _exct_decode] detections", detections.shape)

            return detections
            #return self._decode(*outs[-9:], **kwargs)


class CTLoss(nn.Module):#yezheng: 
    def __init__(self, regr_weight=1, focal_loss=_neg_loss):
        super(CTLoss, self).__init__()

        self.regr_weight = regr_weight
        self.focal_loss  = focal_loss
        self.regr_loss   = _regr_loss

    def forward(self, outs, targets):
        stride = 9

        t_heats  = outs[0::stride]
        l_heats  = outs[1::stride]
        b_heats  = outs[2::stride]
        r_heats  = outs[3::stride]
        ct_heats = outs[4::stride]
        t_regrs  = outs[5::stride]
        l_regrs  = outs[6::stride]
        b_regrs  = outs[7::stride]
        r_regrs  = outs[8::stride]

        gt_t_heat  = targets[0]
        gt_l_heat  = targets[1]
        gt_b_heat  = targets[2]
        gt_r_heat  = targets[3]
        gt_ct_heat = targets[4]
        gt_mask    = targets[5]
        gt_t_regr  = targets[6]
        gt_l_regr  = targets[7]
        gt_b_regr  = targets[8]
        gt_r_regr  = targets[9]

        # focal loss
        focal_loss = 0

        t_heats  = [_sigmoid(t) for t in t_heats]
        l_heats  = [_sigmoid(l) for l in l_heats]
        b_heats  = [_sigmoid(b) for b in b_heats]
        r_heats  = [_sigmoid(r) for r in r_heats]
        ct_heats = [_sigmoid(ct) for ct in ct_heats]

        focal_loss += self.focal_loss(t_heats, gt_t_heat)
        focal_loss += self.focal_loss(l_heats, gt_l_heat)
        focal_loss += self.focal_loss(b_heats, gt_b_heat)
        focal_loss += self.focal_loss(r_heats, gt_r_heat)
        focal_loss += self.focal_loss(ct_heats, gt_ct_heat)

        # regression loss
        regr_loss = 0
        for t_regr, l_regr, b_regr, r_regr in \
          zip(t_regrs, l_regrs, b_regrs, r_regrs):
            regr_loss += self.regr_loss(t_regr, gt_t_regr, gt_mask)
            regr_loss += self.regr_loss(l_regr, gt_l_regr, gt_mask)
            regr_loss += self.regr_loss(b_regr, gt_b_regr, gt_mask)
            regr_loss += self.regr_loss(r_regr, gt_r_regr, gt_mask)
        regr_loss = self.regr_weight * regr_loss

        loss = (focal_loss + regr_loss) / len(t_heats)
        return loss.unsqueeze(0)

def _debug(image, t_heat, l_heat, b_heat, r_heat, ct_heat):
    debugger = Debugger(num_classes=3)
    k = 0

    t_heat = torch.sigmoid(t_heat)
    l_heat = torch.sigmoid(l_heat)
    b_heat = torch.sigmoid(b_heat)
    r_heat = torch.sigmoid(r_heat)
    
    
    aggr_weight = 0.1
    t_heat = _h_aggregate(t_heat, aggr_weight=aggr_weight)
    print("[exkp.py _debug] final t_heat", t_heat.shape)
    l_heat = _v_aggregate(l_heat, aggr_weight=aggr_weight)
    b_heat = _h_aggregate(b_heat, aggr_weight=aggr_weight)
    r_heat = _v_aggregate(r_heat, aggr_weight=aggr_weight)
    t_heat[t_heat > 1] = 1
    l_heat[l_heat > 1] = 1
    b_heat[b_heat > 1] = 1
    r_heat[r_heat > 1] = 1
    
    
    ct_heat = torch.sigmoid(ct_heat)

    t_hm = debugger.gen_colormap(t_heat[k].cpu().data.numpy())
    l_hm = debugger.gen_colormap(l_heat[k].cpu().data.numpy())
    b_hm = debugger.gen_colormap(b_heat[k].cpu().data.numpy())
    r_hm = debugger.gen_colormap(r_heat[k].cpu().data.numpy())
    ct_hm = debugger.gen_colormap(ct_heat[k].cpu().data.numpy())

    hms = np.maximum(np.maximum(t_hm, l_hm), 
                     np.maximum(b_hm, r_hm))
    # debugger.add_img(hms, 'hms')
    if image is not None:
        mean = np.array([0.40789654, 0.44719302, 0.47026115],
                        dtype=np.float32).reshape(3, 1, 1)
        std = np.array([0.28863828, 0.27408164, 0.27809835],
                        dtype=np.float32).reshape(3, 1, 1)
        img = (image[k].cpu().data.numpy() * std + mean) * 255
        img = img.astype(np.uint8).transpose(1, 2, 0)
        debugger.add_img(img, 'img')
        # debugger.add_blend_img(img, t_hm, 't_hm')
        # debugger.add_blend_img(img, l_hm, 'l_hm')
        # debugger.add_blend_img(img, b_hm, 'b_hm')
        # debugger.add_blend_img(img, r_hm, 'r_hm')
        debugger.add_blend_img(img, hms, 'extreme')
        debugger.add_blend_img(img, ct_hm, 'center')
    debugger.show_all_imgs(pause=False)
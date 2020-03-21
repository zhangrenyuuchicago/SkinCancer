import torchvision
import torch
import SlideDataset
import pickle
import ntpath
import os
import numpy as np
import torch.nn.functional as F
from tensorboardX import SummaryWriter
from optparse import OptionParser
from datetime import datetime
from sklearn import metrics
import glob
from Model import *
from keras.utils import to_categorical

usage = "usage: python msi_advr.py "
parser = OptionParser(usage)

parser.add_option("-b", "--batch_size", dest="batch_size", type="int", default=32,
                    help="batch size")

(options, args) = parser.parse_args()

batch_size = options.batch_size
embed_len = 10
inst_num = 20
test_times = 10

minet = MINet(embed_len, inst_num )
minet = torch.nn.DataParallel(minet).cuda()
soft_layer = torch.nn.Softmax(dim=1)

loss_fn_label = torch.nn.CrossEntropyLoss().cuda()

val_transform = torchvision.transforms.Compose([
            torchvision.transforms.Resize(224),
            torchvision.transforms.ToTensor()
        ])

slide_acc_lt = []
label_index = {'None-cancer':0, 'bowens1':1, 'scc1':2, 'bcc1':3}
label_auc = {'None-cancer':[], 'bowens1':[], 'scc1':[], 'bcc1':[]}
macro_avg_auc = []

confusion_matrix_lt = []

for checkpoint_file in glob.glob('./*.pt'):
    checkpoint_name = os.path.basename(checkpoint_file)
    checkpoint_name = checkpoint_name[:-3]
    array = checkpoint_name.split("_")
    fold = int(array[4])
    print(f'current fold: {fold}')
    test_image_data = SlideDataset.SlideDataset('test', '../../settings/image_biopsy.csv',
        '../../gen_tiles/tiles_20X_1000/tiles_20X_1000/',
        val_transform, fold, inst_num)
    test_data_loader = torch.utils.data.DataLoader(test_image_data, num_workers=6, batch_size=7)

    checkpoint = torch.load(checkpoint_file)
    minet.load_state_dict(checkpoint['minet'])
    best_epoch = checkpoint['epoch']
    best_val_auc = checkpoint['best_val_auc']
    print(f'best epoch: {best_epoch}, best val macro-average auc: {best_val_auc}')    
    ground_truth_lt = []
    pred_lt = []
    img_name_lt = []
    weight_lt = []
    count = 0
    sum_loss = 0.0
    minet.eval()
    for _ in range(test_times):
        for id, (item, label, img_name) in enumerate(test_data_loader):
            item = item.cuda()
            label = label.cuda()
            input_var = torch.autograd.Variable(item, requires_grad=False)
            label = torch.squeeze(label, dim=1)
            label_var = torch.autograd.Variable(label)
            with torch.no_grad():
                label_pred, out_weight  = minet(input_var)
                label_soft_pred = soft_layer(label_pred)
            loss_label = loss_fn_label(label_pred, label_var)
            loss = loss_label

            cur_loss = loss.data.cpu().numpy()
            sum_loss += cur_loss
            count += 1
    
            label_pred_np = label_soft_pred.data.cpu().numpy()
            pred_lt += list(label_pred_np)
            ground_truth_lt += list(label_var.data.cpu().numpy())
            img_name_lt += list(img_name)
            weight_lt += list(out_weight.data.cpu().numpy())

    ground_truth_lt = np.array(ground_truth_lt)
    #cata_ground_truth_lt = to_categorical(ground_truth_lt)
    pred_lt = np.array(pred_lt)
    
    img_pred = {}
    img_ground_truth = {}
    for i in range(len(img_name_lt)):
        img_name = img_name_lt[i]
        if img_name not in img_pred:
            img_pred[img_name] = [pred_lt[i]]
        else:
            img_pred[img_name].append(pred_lt[i])
        if img_name in img_ground_truth:
            assert img_ground_truth[img_name] == ground_truth_lt[i]
        else:
            img_ground_truth[img_name] = ground_truth_lt[i]

    for img_name in img_pred:
        img_pred[img_name] = np.mean(np.array(img_pred[img_name]),axis=0)
    
    ground_truth_lt = []
    pred_lt = []
    for img_name in img_pred:
        pred_lt.append(img_pred[img_name])
        ground_truth_lt.append(img_ground_truth[img_name])
    
    ground_truth_lt = np.array(ground_truth_lt)
    pred_lt = np.array(pred_lt)
    cata_ground_truth_lt = to_categorical(ground_truth_lt)
    
    pred_index_lt = np.argmax(pred_lt, axis=1)
    slide_acc = metrics.accuracy_score(ground_truth_lt, pred_index_lt)
    print( f"Test slide acc: {slide_acc}")
    conf_matrix = metrics.confusion_matrix(ground_truth_lt, pred_index_lt)
    print('Confusion matrix:')
    print(conf_matrix)
    confusion_matrix_lt.append(conf_matrix)

    slide_acc_lt.append(slide_acc)
    for label in label_index:
        mask_truth_lt = (ground_truth_lt == label_index[label])
        mask_pred_lt = pred_lt[:, label_index[label]]
        auc = metrics.roc_auc_score(mask_truth_lt, mask_pred_lt)
        label_auc[label].append(auc) 
        print(f'\tLABEL: {label}, auc: {auc}')

    ma_auc = metrics.roc_auc_score(cata_ground_truth_lt, pred_lt, average='macro')
    macro_avg_auc.append(ma_auc)
    print(f'Macro average AUC: {ma_auc}')

import scipy.stats
import math

def mean_confidence_interval(data, confidence=0.95):
    a = 1.0 * np.array(data)
    n = len(a)
    m, se = np.mean(a), scipy.stats.sem(a)
    h = math.sqrt(n) * se * scipy.stats.t.ppf((1 + confidence) / 2., n-1)
    return m, m-h, m+h

print('Overall result')
mean, lower, upper = mean_confidence_interval(slide_acc_lt, confidence=0.95)
print(f'ACC Mean: {mean}, Confidence Interval(0.95): [{lower}, {upper}]')

model_num = len(confusion_matrix_lt) 
confusion_matrix_lt = np.array(confusion_matrix_lt)
confusion_matrix = np.sum(confusion_matrix_lt, axis=0) / (model_num*7)

from sklearn.preprocessing import normalize
confusion_matrix = normalize(confusion_matrix, axis=1, norm='l1')

print(f'Confusion matrix:')
print(confusion_matrix)
np.savetxt('confusion_matrix.txt', confusion_matrix)
for label in label_auc:
    auc_lt = label_auc[label]
    mean, lower, upper = mean_confidence_interval(auc_lt, confidence=0.95)
    print(f'\tLABEL: {label}, AUC Mean: {mean}, Confidence Interval(0.95): [{lower}, {upper}]')

mean, lower, upper = mean_confidence_interval(macro_avg_auc, confidence=0.95)
print(f'Micro Average AUC Mean: {mean}, Confidence Interval(0.95): [{lower}, {upper}]')

import json

result = {'label_auc': label_auc,
        'confusion_matrix': confusion_matrix.tolist(),
        'slide_acc_lt':slide_acc_lt}

with open('result_test.json', 'w') as outfile:  
    json.dump(result, outfile)


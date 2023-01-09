from pickle import UnpicklingError
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader,Dataset, random_split
import os
import argparse
from torch.optim import Adam
from utils.constants import SCRATCH, MAX_LEN, CHECKPOINTS_SCRATCH
from torch.nn.utils.rnn import pad_sequence
import time
# from torchmetrics import MeanAbsolutePercentageError


DATA_PATH=os.path.join(SCRATCH, "mha_outputs")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")  # checking whether you have a GPU, I hope so!
devices=list(range(torch.cuda.device_count()))

def MAPE(target, output):
    #Mean Absolute Percentage Error

    with torch.no_grad():
        relative_error = torch.abs(output - target) / torch.max(torch.abs(target), torch.ones(output.shape, device = device)*1e-32)
        return torch.mean(relative_error)
class FFDataset(Dataset):
    def __init__(self, data, masks, labels):
        self.data = torch.tensor(data,device=device)
        self.labels = torch.tensor(labels,device=device)
        self.masks= torch.tensor(masks,device=device)
        
    def __getitem__(self, index):
        x = self.data[index]
        y = self.masks[index]
        z = self.labels[index]
        return x, y, z
    
    def __len__(self):
        return len(self.data)

class FFNetwork_small(nn.ModuleList):
    def __init__(self, model_dimension=128,sentence_length=MAX_LEN):
        super(FFNetwork_small, self).__init__()
        self.sentence_length=sentence_length
        self.model_dimension=model_dimension
        self.width=self.sentence_length*self.model_dimension
        self.layers=list()
        widths=[1,1,2,2,4,4,2,2,1,1]
        self.depth=len(widths)-1
        self.layers=nn.ModuleList()
        for i in range(self.depth):
            self.layers.extend([nn.LayerNorm(self.width//widths[i]),nn.Linear(self.width//widths[i], self.width//widths[i+1])])
            if(i<self.depth-1):
                self.layers.append(nn.LeakyReLU())
    def forward(self,data,mask):
        for layer in self.layers:
            data=layer(data)
        return data*mask
    
    def init_weights(self):
        for layer in self.layers:
            if(layer==nn.Linear):
                nn.init.uniform_(layer.weight)
                layer.bias.data.fill_(0.01)

class FFNetwork(nn.ModuleList):
    def __init__(self, model_dimension=128,sentence_length=MAX_LEN):
        super(FFNetwork, self).__init__()
        self.sentence_length=sentence_length
        self.model_dimension=model_dimension
        self.width=self.sentence_length*self.model_dimension
        self.layers=list()
        widths=[1,2,4,8,4,1]
        self.depth=len(widths)-1
        self.layers=nn.ModuleList()
        for i in range(self.depth):
            self.layers.extend([nn.LayerNorm(self.width*widths[i]).to(devices[i+1]),nn.Linear(self.width*widths[i], self.width*widths[i+1]).to(devices[i+1])])
            if(i<self.depth-1):
                self.layers.append(nn.LeakyReLU().to(devices[i+1]))
        # self.ln1=nn.LayerNorm(self.width).to(devices[1])
        # self.ff1=nn.Linear(self.width, 2*self.width).to(devices[1])
        # self.nl1=nn.LeakyReLU().to(devices[1])

        # self.ln2=nn.LayerNorm(2*self.width).to(devices[2])
        # self.ff2=nn.Linear(2*self.width, 4*self.width).to(devices[2])
        # self.nl2=nn.LeakyReLU().to(devices[2])

        # self.ln3=nn.LayerNorm(4*self.width).to(devices[3])
        # self.ff3=nn.Linear(4*self.width, 8*self.width).to(devices[3])
        # self.nl3=nn.LeakyReLU().to(devices[3])

        # self.ln4=nn.LayerNorm(8*self.width).to(devices[4])
        # self.ff4=nn.Linear(8*self.width, 4*self.width).to(devices[4])
        # self.nl4=nn.LeakyReLU().to(devices[4])

        # self.ln4=nn.LayerNorm(4*self.width).to(devices[5])
        # self.ff4=nn.Linear(4*self.width, self.width).to(devices[5])
        

    def forward(self,data,mask):
        # data=data.to(devices[1])
        # data=self.ln1(data)
        # data=self.ff1(data)
        # data=self.nl1(data)

        # data=data.to(devices[2])
        # data=self.ln2(data)
        # data=self.ff2(data)
        # data=self.nl2(data)

        # data=data.to(devices[3])
        # data=self.ln3(data)
        # data=self.ff3(data)
        # data=self.nl3(data)

        # data=data.to(devices[4])
        # data=self.ln4(data)
        # data=self.ff4(data)
        # data=self.nl4(data)

        # data=data.to(devices[5])
        # data=self.ln5(data)
        # data=self.ff5(data)
        for (i,layer) in enumerate(self.layers):
            if(i%3==0):
                data=data.to(devices[i//3+1])
            data=layer(data)
        data=data.to(devices[0])
        mask=mask.to(devices[0])
        return data*mask
    
    def init_weights(self):
        for layer in self.layers:
            if(layer==nn.Linear):
                nn.init.uniform_(layer.weight)
                layer.bias.data.fill_(0.01)
                
def prepare_data(data_path, chosen_layer = 0, batch_size = 5, t = "train", dev = False):
    if t not in ["train", "test", "val"]:
        raise ValueError("ERROR: t must be train, test, or val.")
    in_path =   os.path.join(data_path,f"128emb_20ep_IWSLT_E2G_layer{chosen_layer}_v_inputs_{t}")
    out_path =  os.path.join(data_path,f"128emb_20ep_IWSLT_E2G_layer{chosen_layer}_outputs_{t}")
    mask_path = os.path.join(data_path,f"128emb_20ep_IWSLT_E2G_masks_{t}")
    dataset = FixedWordsInterResultsDataset(in_path, out_path, mask_path, MAX_LEN)
    if dev:
        dataset, _ = dataset = random_split(dataset, [0.2, 0.8])
    return DataLoader(dataset,  collate_fn=collate_batch, batch_size= batch_size)
    
    
def training_replacement_FF(params):
    model=FFNetwork().to(device)
    #model.init_weights()
    model.train(True)
    print("FF model created")
    lr_optimizer = Adam(model.parameters(), lr=0.0001,betas=(0.9, 0.98), eps=1e-9)
    print("Preparing data")
    data_loader=prepare_data(params['dataset_path'], chosen_layer = params['num_of_curr_trained_layer'], batch_size = params["batch_size"]) 
      
    mse_loss=nn.MSELoss()
    # mean_abs_percentage_error = MeanAbsolutePercentageError()
    for epoch in range(params['num_of_epochs']):
        print("Epoch: ",epoch)
        epoch_loss=0
        num_embeddings=0
        mapes = []
        start = time.time()
        for (data,label, mask) in data_loader:
            lr_optimizer.zero_grad()
            pred=model(data,mask)
            with torch.no_grad():
                num_embeddings+=torch.sum(torch.flatten(mask)).item()
                loss_normalizer=torch.sum(torch.flatten(mask)).item()/(mask.shape[0]*mask.shape[1])
            loss=mse_loss(label,pred)/loss_normalizer
            loss.backward()
            lr_optimizer.step()
            with torch.no_grad():
                epoch_loss+=loss.item()*torch.sum(torch.flatten(mask)).item()
                mapes.append(MAPE(label, pred))
        if epoch % 20 == 0:
            ckpt_model_name = f"transformer_ckpt_epoch_{epoch + 1}_layer_{params['num_of_curr_trained_layer']}.pth"
            torch.save(model.state_dict(), os.path.join(params["checkpoints_folder"], ckpt_model_name))
        print(f"Loss per embedding element:{epoch_loss/num_embeddings}, MAPE: {MAPE(label, pred)}, time: {time.time() - start}")

class FixedWordsInterResultsDataset(torch.utils.data.Dataset):
    def __init__(self, input_path, output_path, mask_path, n, t = "max"):
        print(f"Starting to load datasets from {input_path} and {output_path} and {mask_path}")
        start = time.time()

        self.n = n
        if t != "max" and t != "exact":
            raise ValueError("ERROR: t has to be either 'max' or 'exact'.")
        self.t = t
        self.input = []
        self.output = []
        if t == "max":
            self.mask = []
            mask_cache = f"{mask_path}_fixed_{n}_{t}.cache"

        in_cache = f"{input_path}_fixed_{n}_{t}.cache"
        out_cache = f"{output_path}_fixed_{n}_{t}.cache"

        if os.path.exists(in_cache) and os.path.exists(out_cache) and (t == "exact" or os.path.exists(mask_cache)):
            self.input = torch.load(in_cache)
            self.output = torch.load(out_cache)
            if t == "max":
                self.mask = torch.load(mask_cache)
                print(f"Finished loading mask dataset from cache {mask_cache}")
            print(f"Finished loading datasets from cache {in_cache} and {out_cache}")
            print(f"Loaded {len(self.output)} samples in {time.time() - start}s")
            return

        inf = open(input_path, "rb")
        outf = open(output_path, "rb")
        maskf = open(mask_path, "rb")
        try:
            while(True):
                # i represents one batch of sentences -> dim: batch size x padded sentence length x embedding size
                i = torch.from_numpy(np.load(inf))
                m = torch.from_numpy(np.load(maskf))
                m = torch.squeeze(m, dim=1)
                m = torch.squeeze(m, dim=1)
                o = torch.from_numpy(np.load(outf))
                l = torch.sum(m, dim = 1)
                for j in range(i.shape[0]):
                    if t == "max":
                        if l[j] <= n:
                            self.input.append(i[j, :l[j]])
                            self.output.append(o[j,:,:l[j]])
                            self.mask.append(m[j,  :l[j]])
                    else:
                        if l[j] == n:
                            self.input.append(i[j, :l[j]])
                            self.output.append(o[j, :l[j]])
        except (UnpicklingError, ValueError):
            print(f"Finished loading datasets from {input_path} and {output_path}")
            print(f"Loaded {len(self.output)} samples in {time.time() - start}s")
        finally:
            inf.close()
            outf.close()
            maskf.close()
        # self.input = torch.cat(self.input, dim=0)
        # self.output = torch.cat(self.output, dim=0)
        print(self.input[0].shape)
        torch.save(self.input, in_cache)
        torch.save(self.output, out_cache)
        if t == "max":
            # self.mask = torch.cat(self.mask, dim=0)
            torch.save(self.mask, mask_cache)

    def __len__(self):
        return len(self.input)

    def __getitem__(self, idx):
        # if we have exactly the same length, there is no need for padding/masking
        if self.t == "exact":
            return (self.input[idx], self.output[idx])
        return (self.input[idx], self.output[idx], self.mask[idx])

    def emb_size(self):
        return self.input.shape[1]
    
def pad_shape(batch, masks = False):
    shape = batch.shape
    if masks:
        return shape[0],MAX_LEN-shape[1] 
    return shape[0], MAX_LEN-shape[1], shape[2]

def collate_batch(batch):   
    # print("COLLATE")
    # print(batch[0][0].shape)
    # print(batch[0][1].shape)
    # print(batch[0][2].shape)
    
    # Pad all elements to the same length
    NH = batch[0][1].shape[0]
    HD = batch[0][1].shape[2]
    inputs  = pad_sequence([x[0] for x in batch], batch_first=True, padding_value=0)
    outputs = pad_sequence([x[1].transpose(0,1).reshape(-1, NH * HD) for x in batch], batch_first=True, padding_value=0) # this reshaping must be transfered to the adapter as well
    masks   = pad_sequence([x[2] for x in batch], batch_first=True, padding_value=0) 
    # print(inputs.shape)
    # print(outputs.shape)
    # print(masks.shape)
    
    # Pad to fixed length
    inputs = torch.cat([inputs, torch.zeros(pad_shape(inputs))], dim = 1).to(device)
    outputs = torch.cat([outputs, torch.zeros(pad_shape(outputs))], dim = 1).to(device)
    masks = torch.cat([masks, torch.zeros(pad_shape(masks, masks = True), dtype=torch.bool)], dim = 1).to(device)
    
    # Reshape concatenating the embeddings for each sentence
    masks = torch.repeat_interleave(masks, inputs.shape[-1] ,dim=1)
    inputs = torch.reshape(inputs, (inputs.shape[0],inputs.shape[1]*inputs.shape[2]))
    outputs = torch.reshape(outputs, (outputs.shape[0],outputs.shape[1]*outputs.shape[2]))
    return inputs, outputs, masks

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--num_of_epochs", type=int, help="number of training epochs", default=150)
    parser.add_argument("--dataset_path", type=str, help='download dataset to this path', default=DATA_PATH)
    parser.add_argument("--model_dimension", type=str, help='embedding size', default=128)
    parser.add_argument("--num_of_loaded_files", type=str, help='num_of_loaded_files', default=20)
    parser.add_argument("--num_of_curr_trained_layer", type=str, help='num_of_curr_trained_layer', default=0)
    parser.add_argument("--batch_size", type=str, help='batch_size', default=500)
    parser.add_argument("--checkpoints_folder_name", type = str, help="folder name relative to checkpoint folder")
    args = parser.parse_args()
    # Wrapping training configuration into a dictionary
    training_config = dict()
    for arg in vars(args):
        training_config[arg] = getattr(args, arg)
    print("Training arguments parsed")
    training_config["checkpoints_folder"] = os.path.join(CHECKPOINTS_SCRATCH, training_config["checkpoints_folder_name"])
    os.makedirs(training_config["checkpoints_folder"], exist_ok = True)
    print(training_config["checkpoints_folder"])
    training_replacement_FF(training_config)

import numpy as np
import matplotlib.pyplot as plt
import os
import torch
import torchvision
import torchvision.transforms as transforms
from torch.utils.data.sampler import SubsetRandomSampler
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import pandas as pd
from torchvision import models
from torchvision.datasets.folder import default_loader
from torch.utils.data import Dataset, DataLoader

# from torchsummary import summary

device = torch.device('cuda:3' if torch.cuda.is_available() else 'cpu')


class CubsDataset(Dataset):
    base_dir = 'CUB_200_2011/images'

    def __init__(self, root, train=True, transform=None, loader=default_loader):
        self.root = os.path.expanduser(root)
        self.transform = transform
        self.train = train
        self.loader = default_loader
        self.__load_metadata__()
        print('init')

    def __load_metadata__(self):
        images = pd.read_csv(os.path.join(self.root, 'CUB_200_2011', 'images.txt'), sep=' ',
                             names=['image_id', 'image_name'])
        labels = pd.read_csv(os.path.join(self.root, 'CUB_200_2011', 'image_class_labels.txt'), sep=' ',
                             names=['image_id', 'class_id'])
        train_test_split = pd.read_csv(os.path.join(self.root, 'CUB_200_2011', 'train_test_split.txt'), sep=' ',
                                       names=['image_id', 'is_training_image'])
        class_labels = pd.read_csv(os.path.join(self.root, 'CUB_200_2011', 'classes.txt'), sep=' ',
                                   names=['class_id', 'class_name'])

        data = images.merge(labels, on='image_id')
        self.data = data.merge(train_test_split, on='image_id')

        if self.train:
            self.data = self.data[self.data.is_training_image == 1]
        else:
            self.data = self.data[self.data.is_training_image == 0]

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        sample = self.data.iloc[index]
        path = os.path.join(self.root, self.base_dir, sample.image_name)
        image = self.loader(path)
        # image = image/255
        class_id = sample.class_id - 1

        # print(self.transform)
        if self.transform:
            image = self.transform(image)

        return image, class_id

# transforms for training set
transforms_train = transforms.Compose([transforms.Resize((224, 224)),
                                         transforms.RandomHorizontalFlip(),
                                         transforms.ToTensor() 
                                         ])
# transforms for testing set
transforms_test = transforms.Compose([transforms.Resize((224,224)), 
                                         transforms.ToTensor()
                                         ])

cubs_dataset_train = CubsDataset(root='datasets', transform=transforms_train)  # train #can use loader=pil_image
cubs_dataset_test = CubsDataset(root='datasets', train=False, transform=transforms_test)
print(len(cubs_dataset_train))
print(len(cubs_dataset_test))
print('loaded train and test sets')

### create validation set


validation_split = .05
shuffle_dataset = True
random_seed= 43
dataset_size = len(cubs_dataset_train)
indices = list(range(dataset_size))
split = int(np.floor(validation_split * dataset_size))
if shuffle_dataset :
    np.random.seed(random_seed)
    np.random.shuffle(indices)
train_indices, val_indices = indices[split:], indices[:split]

# Creating PT data samplers and loaders:
train_sampler = SubsetRandomSampler(train_indices)
valid_sampler = SubsetRandomSampler(val_indices)



#pretrained resnet-50 backbone to the Siamese learning method
resnet = models.resnet50(pretrained=True)

# resnet.to(device)
# summary(resnet, (3,224,224))

resnet.fc = nn.Sequential(
    nn.Linear(2048, 200, bias=True)
)
print('training about to start')

resnet = resnet.float().to(device)
criterion = nn.CrossEntropyLoss()

## Learning with Stochastic Gradient Descent
optimizer = torch.optim.SGD(resnet.parameters(), lr=1e-3, momentum=0.9, weight_decay=0.0001)
# optimizer = torch.optim.Adamax(model.parameters(), lr=0.01)
num_epochs = 60
num_classes = 200
file=open('cs_log.txt', 'a')

loss_epoch = []
accuracy_epoch = []

#define cosine similarity
cos_similarity = nn.CosineSimilarity(dim=1, eps=1e-6)
gamma = 0
l_ambda = 2
batch_size = 12

## keeping an adaptive learning rate that decays with time
scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=15, gamma=0.1)
# scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.95)

for epoch in range(num_epochs):
    # generating a shuffled dataloader of training set
  D1 = DataLoader(cubs_dataset_train, batch_size=batch_size, 
                                           sampler=train_sampler)
  D2 = DataLoader(cubs_dataset_train, batch_size=batch_size, 
                                           sampler=train_sampler)
  total_step = len(D1)
  t_loss = 0
  for index, ((d1_image, d1_label), (d2_image, d2_label)) in enumerate(zip(D1, D2)):
        # sending all images to GPU (if available, defaults to cpu if gpu unavailable)

        loss_batch = 0
        ec_batch = 0
        count = 0
        len_batch = len(d1_image)

        d1_image = d1_image.to(device)
        d11_label = d1_label.to(device)
        d2_image = d2_image.to(device)
        d22_label = d2_label.to(device)

        # get output of model for both images wrt their own respective classes
        # Siamese behavior of the network is shown here
        op1 = F.normalize(resnet(d1_image))
        loss1 = criterion(op1, d11_label)

        op2 = F.normalize(resnet(d2_image))
        loss2 = criterion(op2, d22_label)


        cs_batch = sum(cos_similarity(op1,op2))
        loss_batch = loss1 + loss2 + l_ambda*cs_batch / len_batch
        t_loss += loss_batch.item()
        
        optimizer.zero_grad()
        loss_batch.backward()
        optimizer.step()
        if (index + 1) % 50 == 0:
            print("Epoch: ", (epoch + 1), " Step: ", (index + 1), " /", total_step, " Loss: ", loss_batch.item())


  epoch_loss = t_loss/total_step
  loss_epoch.append(loss_batch.item())
  print("Epoch: ", (epoch + 1), " Loss: ", loss_batch.item())
  for param_group in optimizer.param_groups:
      print("lr: ", param_group['lr'])
  
  
  ## perform validation after each epoch
  with torch.no_grad():
       correct = 0
       total = 0
       v_loss = 0
       D3 = DataLoader(cubs_dataset_train, batch_size=batch_size,
                    sampler=valid_sampler)
       for ind, (images, labels) in enumerate(D3):
            images = images.to(device)
            labels = labels.to(device)
            outputs = resnet(images)
            v_loss += criterion(outputs, labels)
            _, predicted = torch.max(outputs.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
  
       acc = 100*correct/total
       accuracy_epoch.append(acc)
       print('Accuracy: ', acc, " Validation Loss: ", v_loss.item())
       # string = "Epoch: "+str(epoch) + " Accuracy: "+ str(acc)+"\n"
       string2 = "Epoch: " + str(epoch) +" Training Loss: "+str(epoch_loss)+ " Validation Loss: " + str(v_loss.item()/len(D3)) + "\n"
       file.write(string2)
       if v_loss.item() < 96.00:
           checkpoint = {
              'model': resnet, 
              'state_dict': resnet.state_dict(), 
              'optimizer': optimizer.state_dict()
              }
           mod_name = "model/cosine_new_run/model33_1_" + str(epoch) + ".pth"
           # torch.save(checkpoint, mod_name)
  scheduler.step()

print("finished training")
# file.close()
# gc.collect()

print('testing now..')
resnet.eval()  # eval mode 
with torch.no_grad():
    correct = 0
    total = 0
    D3 = DataLoader(cubs_dataset_test, batch_size=60, shuffle=False)
    for ind, (images, labels) in enumerate(D3):
        images = images.to(device)
        labels = labels.to(device)
        outputs = resnet(images)
        _, predicted = torch.max(outputs.data, 1)
        total += labels.size(0)
        correct += (predicted == labels).sum().item()

    final = 100*correct/total
    print('Test Accuracy of the model: {} %'.format(100*correct / total))
    string_final = "Test Accuracy of the model: " + str(final)+"\n"
    file.write(string_final)


file.close()


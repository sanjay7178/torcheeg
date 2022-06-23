import logging
import os
import random

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data.dataloader import DataLoader
from torcheeg import transforms
from torcheeg.datasets import DEAPDataset
from torcheeg.datasets.constants.emotion_recognition.deap import \
    DEAP_CHANNEL_LOCATION_DICT
from torcheeg.model_selection import KFoldPerSubject, train_test_split
from torcheeg.models import CCNN


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def train(dataloader, model, loss_fn, optimizer):
    size = len(dataloader.dataset)
    model.train()
    for batch_idx, batch in enumerate(dataloader):
        X = batch[0].to(device)
        y = batch[1].to(device)

        # Compute prediction error
        pred = model(X)
        loss = loss_fn(pred, y)

        # Backpropagation
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if batch_idx % 100 == 0:
            loss, current = loss.item(), batch_idx * len(X)
            print(f"Loss: {loss:>7f}  [{current:>5d}/{size:>5d}]")

    return loss


def valid(dataloader, model, loss_fn):
    size = len(dataloader.dataset)
    num_batches = len(dataloader)
    model.eval()
    loss, correct = 0, 0
    with torch.no_grad():
        for batch in dataloader:
            X = batch[0].to(device)
            y = batch[1].to(device)

            pred = model(X)
            loss += loss_fn(pred, y).item()
            correct += (pred.argmax(1) == y).type(torch.float).sum().item()
    loss /= num_batches
    correct /= size
    print(f"Valid Error: \n Accuracy: {(100*correct):>0.1f}%, Avg loss: {loss:>8f} \n")

    return correct, loss


if __name__ == "__main__":
    seed_everything(42)

    os.makedirs("./tmp_out/examples_ccnn", exist_ok=True)

    logger = logging.getLogger('examples_tsception')
    logger.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler('./tmp_out/examples_ccnn/examples_tsception.log')
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    dataset = DEAPDataset(io_path=f'./tmp_out/examples_ccnn/deap',
                          root_path='./tmp_in/data_preprocessed_python',
                          offline_transform=transforms.Compose([
                              transforms.BandDifferentialEntropy(apply_to_baseline=True),
                              transforms.BaselineRemoval(),
                              transforms.ToGrid(DEAP_CHANNEL_LOCATION_DICT)
                          ]),
                          online_transform=transforms.ToTensor(),
                          label_transform=transforms.Compose([
                              transforms.Select('valence'),
                              transforms.Binary(5.0),
                          ]),
                          num_worker=8)

    k_fold = KFoldPerSubject(n_splits=10, split_path=f'./tmp_out/examples_ccnn/split', shuffle=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    loss_fn = nn.CrossEntropyLoss()
    batch_size = 64

    test_accs = []
    test_losses = []

    for i, (train_dataset, test_dataset) in enumerate(k_fold.split(dataset)):

        model = CCNN(num_classes=2, in_channels=4, grid_size=(9, 9)).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)  # official: weight_decay=5e-1

        train_dataset, val_dataset = train_test_split(train_dataset,
                                                              test_size=0.2,
                                                              split_path=f'./tmp_out/examples_ccnn/split{i}',
                                                              shuffle=True)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=True)

        epochs = 50
        best_val_acc = 0.0
        for t in range(epochs):
            train_loss = train(train_loader, model, loss_fn, optimizer)
            val_acc, val_loss = valid(val_loader, model, loss_fn)

            if val_acc > best_val_acc:
                best_val_acc = val_acc
                torch.save(model.state_dict(), f'./tmp_out/examples_ccnn/model{i}.pt')

        test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

        model.load_state_dict(torch.load(f'./tmp_out/examples_ccnn/model{i}.pt'))
        test_acc, test_loss = valid(test_loader, model, loss_fn)

        logger.info(f"Test Error {i}: \n Accuracy: {(100*test_acc):>0.1f}%, Avg loss: {test_loss:>8f}")

        test_accs.append(test_acc)
        test_losses.append(test_loss)

    logger.info(f"Test Error: \n Accuracy: {100*np.mean(test_accs):>0.1f}%, Avg loss: {np.mean(test_losses):>8f}")

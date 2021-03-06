import io
import json
from typing import Optional # required for "Optional[type]"
from PIL import Image
import pandas as pd
from flask import Flask, request,send_from_directory
import os
import cv2
import pydicom
import png
import numpy as np
import matplotlib.pyplot as plt
import sys

import torch,torchvision
from torch import nn
from torch import Tensor
from torchvision import models
import torchvision.transforms as transforms
import torch
import torchvision
from Utils import use_gradcam

from flask_cors import CORS

from pathlib import Path

app = Flask(__name__)
app.config["DEBUG"] = True
CORS(app)
UPLOAD_FOLDER = './input_folder'
GRADCAM_FOLDER='./gradcam_imgs'
ALLOWED_EXTENSIONS = {'png', 'dcm'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

################ START_OF_MODEL ################
#Code modified and taken from Andrea de Luca (https://bit.ly/2YXW6xN)
device = torch.device("cpu")

class Flatten(nn.Module):
    "Flatten `x` to a single dimension, often used at the end of a model. `full` for rank-1 tensor"
    def __init__(self, full:bool=False):
        super().__init__()
        self.full = full

    def forward(self, x):
        return x.view(-1) if self.full else x.view(x.size(0), -1)

class AdaptiveConcatPool2d(nn.Module):
    "Layer that concats `AdaptiveAvgPool2d` and `AdaptiveMaxPool2d`." # from pytorch
    def __init__(self, sz:Optional[int]=None):
        "Output will be 2*sz or 2 if sz is None"
        super().__init__()
        self.output_size = sz or 1
        self.ap = nn.AdaptiveAvgPool2d(self.output_size)
        self.mp = nn.AdaptiveMaxPool2d(self.output_size)
    def forward(self, x):
        return torch.cat([self.mp(x), self.ap(x)], 1)

def myhead(nf, nc):
    '''
    Inputs: nf=  # of in_features in the 4th layer , nc= # of classes
    '''
    return \
    nn.Sequential(        # the dropout is needed otherwise you cannot load the weights
            AdaptiveConcatPool2d(),
            Flatten(),
            nn.BatchNorm1d(nf,eps=1e-05,momentum=0.1,affine=True,track_running_stats=True),
            nn.Dropout(p=0.25,inplace=False),
            nn.Linear(nf, 512,bias=True),
            nn.ReLU(inplace=True),
            nn.BatchNorm1d(512,eps=1e-05,momentum=0.1,affine=True,track_running_stats=True),
            nn.Dropout(p=0.5,inplace=False),
            nn.Linear(512, nc,bias=True),
        )


my_model=torchvision.models.resnet34()
modules=list(my_model.children())
modules.pop(-1)
modules.pop(-1)
temp=nn.Sequential(nn.Sequential(*modules))
tempchildren=list(temp.children())

#append the special fastai head
#Configured according to Model Architecture

tempchildren.append(myhead(1024,3))
model_r34=nn.Sequential(*tempchildren)

#LOAD MODEL
state = torch.load(Path('corona_resnet34.pth').resolve(),map_location=torch.device('cpu'))
model_r34.load_state_dict(state['model'])

#important to set to evaluation mode
model_r34.eval()

################ END_OF_MODEL ################

test_transforms = transforms.Compose([
    transforms.Resize(512),
    transforms.CenterCrop(512),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225])
])

#accepts png files
def predict_image(image):
    softmaxer = torch.nn.Softmax(dim=1)
    image_tensor = Image.open(image)
    image_tensor = image_tensor.convert('RGB')
    image_tensor = test_transforms(image_tensor).float()
    image_tensor=image_tensor.unsqueeze(0)

    #convert evaluation to probabilities with softmax
    with torch.no_grad(): #turn off backpropagation
      processed=softmaxer(model_r34(image_tensor))
    return (processed[0]) #return probabilities

def get_metadata(folder,filename, attribute):
    '''
    Given a path to folder of images, patient ID, and attribute, return useful meta-data from the corresponding dicom image.
    IMPLICITLY Converts dicom image to png in the process and puts to test folder
    Returns attribute value, png image (implicit)
    '''
    ds=pydicom.dcmread(folder+'/'+filename+'.dcm')

    #implicit DICOM -> PNG conversion
    shape = ds.pixel_array.shape
    # Convert to float to avoid overflow or underflow losses.
    image_2d = ds.pixel_array.astype(float)
    # Rescaling grey scale between 0-255
    image_2d_scaled = (np.maximum(image_2d,0) / image_2d.max()) * 255.0
    # Convert to uint
    image_2d_scaled = np.uint8(image_2d_scaled)
    # Write the PNG file
    with open(os.path.join(folder,filename+'.png'), 'wb') as png_file:
        w = png.Writer(shape[1], shape[0], greyscale=True)
        w.write(png_file, image_2d_scaled)
    try:
      attribute_value = getattr(ds, attribute)
      return attribute_value
    except: return np.NaN

########Implementation Part###################################
#for original images
@app.route('/uploads/<path:filename>')
def download_file(filename):
    #argument is in the form of filename.extension
    filename=os.path.splitext(os.path.basename(filename))[0]
    if filename[-1]==".":
        filename = filename[:-1]
    if os.path.exists('./input_folder/{}.png'.format(filename)):
        return send_from_directory(UPLOAD_FOLDER,'{}.png'.format(filename), as_attachment=True)

    if os.path.exists('./input_folder/{}.jpg'.format(filename)):
        return send_from_directory(UPLOAD_FOLDER,'{}.jpg'.format(filename), as_attachment=True)

    if os.path.exists('./input_folder/{}.jpeg'.format(filename)):
        return send_from_directory(UPLOAD_FOLDER,'{}.jpeg'.format(filename), as_attachment=True)

#for gradcam images
@app.route('/gradcam/<path:filename>')
def download_gradcam_file(filename):
    #argument is in the form of filename.extension 
    use_gradcam(os.path.join(UPLOAD_FOLDER,filename),GRADCAM_FOLDER,model_r34,test_transforms)
    filename=os.path.splitext(os.path.basename(filename))[0]
    return send_from_directory(GRADCAM_FOLDER,'(gradcam){}.png'.format(filename), as_attachment=True)       

@app.route('/', methods=['POST'])
def predict():
    '''
    Inputs: a list of image filenames ending with an extension (e.x. .png) taken from UPLOAD_FOLDER
    Returns: a json of predictions_df
    '''
    if request.method == 'POST':
        if not os.path.isdir(UPLOAD_FOLDER):
            os.makedirs(UPLOAD_FOLDER)
        if not os.path.isdir(GRADCAM_FOLDER):
            os.makedirs(GRADCAM_FOLDER)
        for filename in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            print(file_path,file=sys.stderr)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    os.shutil.rmtree(file_path)
            except Exception as e:
                print('Failed to delete %s. Reason: %s' % (file_path, e))
        for filename in os.listdir(GRADCAM_FOLDER):
            file_path = os.path.join(GRADCAM_FOLDER, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    os.shutil.rmtree(file_path)
            except Exception as e:
                print('Failed to delete %s. Reason: %s' % (file_path, e))
        data = dict(request.files)
        for key in data.keys():
            data[key].save(os.path.join(UPLOAD_FOLDER,'{}'.format(data[key].filename)))

        print("images saved!")

        #METADATA and CONVERT TO PNG
        #list of files to be converted
        files = [f[:-4]+'.png' for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.dcm')]
        result_df=pd.DataFrame(files,columns=['filename'])

        #list of essential attributes
        attributes = ['PatientID','PatientSex', 'PatientAge', 'ViewPosition']
        for a in attributes:
            result_df[a] = result_df['filename'].apply(lambda x: get_metadata(UPLOAD_FOLDER, x, a))

        #PREDICTION
        #each image in test_files must be a filename.png from the upload folder
        test_files=[file for file in sorted(os.listdir(UPLOAD_FOLDER))if file.endswith(('.png','.jpg','.jpeg'))]
        df_results={filename:predict_image(UPLOAD_FOLDER+'/'+filename) for filename in test_files}
        print("predictions done")
        #OUTPUT DATAFRAMES
        predictions_df=pd.DataFrame.from_dict(df_results,orient='index',columns=['covid','nofinding','opacity']).rename_axis('filename').reset_index()
        predictions_df['covid']=predictions_df['covid'].apply(lambda x: x.item())
        predictions_df['nofinding']=predictions_df['nofinding'].apply(lambda x: x.item())
        predictions_df['opacity']=predictions_df['opacity'].apply(lambda x: x.item())
        #get the column name of the highest probability
        predictions_df['Predicted Label'] =predictions_df[['covid','opacity','nofinding']].idxmax(axis=1)
        print("table done")
        print("gradcam done")
        #predictions_df['filename']=predictions_df['filename'].apply(lambda file: os.path.splitext(file)[0]) #remove .png suffix
        #merge result_df and final_df
        if result_df.empty:
            for a in attributes:
                predictions_df[a]="" #include empty columns for proper json formatting
            final_df=predictions_df
        else:
            final_df=pd.merge(result_df,predictions_df[['filename','Predicted Label']], on='filename')
            #convert age to int to be used later
            final_df['PatientAge'] = pd.to_numeric(final_df['PatientAge'], errors='coerce')

        print("Generating Results!")
        result = final_df.to_json(orient='records') #format: [{"filename":a,... metadata( 'PatientID','PatientSex', 'PatientAge', 'ViewPosition')..., "Predicted Label":f}]
        return result;

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
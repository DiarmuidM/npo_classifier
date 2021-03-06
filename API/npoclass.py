#set up environment
import os, torch, pickle, warnings, random
import pandas as pd
import numpy as np
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler
from transformers import BertForSequenceClassification, BertTokenizer
from tqdm import tqdm, trange
import tensorflow as tf
warnings.filterwarnings("ignore")
from time import sleep

################################### Define functions ##########################
def npoclass(inputs, gpu_core=True, model_path='npoclass_model/', ntee_type='bc'):
    
    # Set the seed value all over the place to make this reproducible.
    seed_val = 42
    random.seed(seed_val)
    np.random.seed(seed_val)
    torch.manual_seed(seed_val)

    # Read model, if not read.
    global model_loaded, tokenizer_loaded, label_encoder
    try:
        assert model_loaded
        assert tokenizer_loaded
        assert label_encoder
    except:
        #load a pretrained model and tokenizer.
        model_loaded = BertForSequenceClassification.from_pretrained(model_path)
        tokenizer_loaded = BertTokenizer.from_pretrained(model_path)
        # Read label encoder.
        if ntee_type=='bc':
            le_file_name='le_broad_cat.pkl'
        elif ntee_type=='mg':
            le_file_name='le_major_group.pkl'
        else:
            raise ValueError("ntee_type must be 'bc' (broad category) or 'mg' (major group)")
        with open(model_path+le_file_name, 'rb') as label_encoder_pkl:
            label_encoder = pickle.load(label_encoder_pkl)
    
    # Select acceleration method.
    if gpu_core==True and torch.cuda.is_available():
        print('There are %d GPU(s) available.' % torch.cuda.device_count(), 'Using GPU:',torch.cuda.get_device_name(0))
        torch.cuda.manual_seed_all(seed_val)
        device = torch.device('cuda')
        model_loaded.cuda()
    else:
        print('No GPU acceleration available or gpu_core=False, using CPU.')
        device = torch.device('cpu')
        model_loaded.cpu()
    print('Encoding inputs ...')
    sleep(.5) # Pause a second for better printing results.
    # Tokenize all of the sentences and map the tokens to thier word IDs.
    input_ids = []
    attention_masks = []
    # Encode inputs.
    def func_encode_string(text_string):
        encoded_dict = tokenizer_loaded.encode_plus(text_string,
                                                    add_special_tokens = True, # Add '[CLS]' and '[SEP]'
                                                    max_length = 256,           # Pad & truncate all sentences.
                                                    pad_to_max_length = True,
                                                    return_attention_mask = True,   # Construct attn. masks.
                                                    return_tensors = 'pt',     # Return pytorch tensors.
                                                   )
        return encoded_dict
    # Encode input string(s).
    if type(inputs)==list:
        for text_string in tqdm(inputs):
            encoded_outputs=func_encode_string(text_string)
            # Add the encoded sentence to the list.
            input_ids.append(encoded_outputs['input_ids'])
            # And its attention mask (simply differentiates padding from non-padding).
            attention_masks.append(encoded_outputs['attention_mask'])
    if type(inputs)==str:
        encoded_outputs=func_encode_string(inputs)
        input_ids=[encoded_outputs['input_ids']]
        attention_masks=[encoded_outputs['attention_mask']]

    # Convert the lists into tensors.
    input_ids = torch.cat(input_ids, dim=0)
    attention_masks = torch.cat(attention_masks, dim=0)

    # Prepare dataloader for efficient calculation.
    batch_size = 32
    pred_data = TensorDataset(input_ids, attention_masks)
    pred_sampler = SequentialSampler(pred_data)
    pred_dataloader = DataLoader(pred_data, sampler=pred_sampler, batch_size=batch_size)

    # Start prediction.
    model_loaded.eval()
    logits_all=[]
    print('Predicting categories ...')
    sleep(.5) # Pause a second for better printing results.
    for batch in tqdm(pred_dataloader):
        # Add batch to the pre-chosen device
        batch = tuple(t.to(device) for t in batch)
        b_input_ids, b_input_mask = batch
        with torch.no_grad():
            outputs = model_loaded(b_input_ids, token_type_ids=None, attention_mask=b_input_mask)
        logits_all+=outputs[0].tolist()

    # Calculate probabilities of logitcs.
    logits_prob=tf.nn.sigmoid(logits_all).numpy().tolist()
    # Find the positions of max values in logits.
    logits_max=np.argmax(logits_prob, axis=1)
    # Transfer to labels.
    logits_labels=label_encoder.inverse_transform(logits_max)
    
    # Compile results to be returned.
    result_list=[]
    for list_index in range(0, len(logits_labels)):
        result_dict={}
        result_dict['recommended']=logits_labels[list_index]
        conf_prob=logits_prob[list_index][logits_max[list_index]]
        if conf_prob>=.99:
            result_dict['confidence']='high (>=.99)'
        elif conf_prob>=.95:
            result_dict['confidence']='medium (<.99|>=.95)'
        else:
            result_dict['confidence']='low (<.95)'
        prob_dict={}
        for label_index in range(0, len(label_encoder.classes_)):
            prob_dict[label_encoder.classes_[label_index]]=logits_prob[list_index][label_index]
        result_dict['probabilities']=prob_dict
        result_list+=[result_dict]
        
    return result_list
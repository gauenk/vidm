
# Copyright (c) 2021, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

import os
import warnings
import os.path as osp
import numpy as np
import PIL.Image
from PIL import Image
from PIL import ImageFile
import json
import torch
import torch.nn.functional as F
from einops import rearrange
from torchvision import transforms
from torchvision.datasets.video_utils import VideoClips
import random
import pickle

from torchvision.datasets import UCF101
from torchvision.datasets.folder import make_dataset
from torch.utils.data import Dataset

try:
    import pyspng
except ImportError:
    pyspng = None


ImageFile.LOAD_TRUNCATED_IMAGES = True
IMG_EXTENSIONS = [
    '.jpg', '.JPG', '.jpeg', '.JPEG',
    '.png', '.PNG', '.ppm', '.PPM', '.bmp', '.BMP',
]

def is_image_file(filename):
    return any(filename.endswith(extension) for extension in IMG_EXTENSIONS)


def pil_loader(path):
    # open path as file to avoid ResourceWarning (https://github.com/python-pillow/Pillow/issues/835)
    '''
    with open(path, 'rb') as f:
        with Image.open(f) as img:
            return img.convert('RGB')
    '''
    Im = Image.open(path)
    return Im.convert('RGB')


def default_loader(path):
    '''
    from torchvision import get_image_backend
    if get_image_backend() == 'accimage':
        return accimage_loader(path)
    else:
    '''
    return pil_loader(path)


def find_classes(dir):
    classes = [d for d in os.listdir(dir) if os.path.isdir(os.path.join(dir, d))]
    classes.sort()
    class_to_idx = {classes[i]: i for i in range(len(classes))}
    return classes, class_to_idx


def make_imageclip_dataset(dir, nframes, class_to_idx, vid_diverse_sampling, split='all'):
    """
    TODO: add xflip
    """
    def _sort(path):
        return sorted(os.listdir(path))

    images = []
    n_video = 0
    n_clip = 0

    dir_list = sorted(os.listdir(dir))
    for target in dir_list:
        if split == 'train':
            if 'val' in target: dir_list.remove(target)
        elif split == 'val' or split == 'test':
            if 'train' in target: dir_list.remove(target)

    for target in dir_list:
        if os.path.isdir(os.path.join(dir,target))==True:
            n_video +=1
            subfolder_path = os.path.join(dir, target)
            for subsubfold in sorted(os.listdir(subfolder_path) ):
                if os.path.isdir(os.path.join(subfolder_path, subsubfold) ):
                    subsubfolder_path = os.path.join(subfolder_path, subsubfold)
                    i = 1

                    if nframes > 0 and vid_diverse_sampling:
                        n_clip += 1

                        item_frames_0 = []
                        item_frames_1 = []
                        item_frames_2 = []
                        item_frames_3 = []

                        for fi in _sort(subsubfolder_path):
                            if is_image_file(fi):
                                file_name = fi
                                file_path = os.path.join(subsubfolder_path, file_name)
                                item = (file_path, class_to_idx[target])

                                if i % 4 == 0:
                                    item_frames_0.append(item)
                                elif i % 4 == 1:
                                    item_frames_1.append(item)
                                elif i % 4 == 2:
                                    item_frames_2.append(item)
                                else:
                                    item_frames_3.append(item)

                                if i %nframes == 0 and i > 0:
                                    images.append(item_frames_0) # item_frames is a list containing n frames.
                                    images.append(item_frames_1) # item_frames is a list containing n frames.
                                    images.append(item_frames_2) # item_frames is a list containing n frames.
                                    images.append(item_frames_3) # item_frames is a list containing n frames.
                                    item_frames_0 = []
                                    item_frames_1 = []
                                    item_frames_2 = []
                                    item_frames_3 = []

                                i = i+1
                    else:
                        item_frames = []
                        for fi in _sort(subsubfolder_path):
                            if is_image_file(fi):
                                # fi is an image in the subsubfolder
                                file_name = fi
                                file_path = os.path.join(subsubfolder_path, file_name)
                                item = (file_path, class_to_idx[target])
                                item_frames.append(item)
                                if i % nframes == 0 and i > 0:
                                    images.append(item_frames)  # item_frames is a list containing 32 frames.
                                    item_frames = []
                                i = i + 1

    return images


def resize_crop(video, resolution):
    """ Resizes video with smallest axis to `resolution * extra_scale`
        and then crops a `resolution` x `resolution` bock. If `crop_mode == "center"`
        do a center crop, if `crop_mode == "random"`, does a random crop
    Args
        video: a tensor of shape [t, c, h, w] in {0, ..., 255}
        resolution: an int
        crop_mode: 'center', 'random'
    Returns
        a processed video of shape [c, t, h, w]
    """
    _, _, h, w = video.shape

    if h > w:
        half = (h - w) // 2
        cropsize = (0, half, w, half + w)  # left, upper, right, lower
    elif w >= h:
        half = (w - h) // 2
        cropsize = (half, 0, half + h, h)

    video = video[:, :, cropsize[1]:cropsize[3],  cropsize[0]:cropsize[2]]
    video = F.interpolate(video, size=resolution, mode='bilinear', align_corners=False)

    video = video.permute(1, 0, 2, 3).contiguous()  # [c, t, h, w]
    return video


def resize_crop_img(image, resolution):
    """ Resizes video with smallest axis to `resolution * extra_scale`
        and then crops a `resolution` x `resolution` bock. If `crop_mode == "center"`
        do a center crop, if `crop_mode == "random"`, does a random crop
    Args
        image: a tensor of shape [c h w] in {0, ..., 255}
        resolution: an int
    Returns
        a processed img of shape [c, h, w]
    """
    # [c h w]
    _, h, w = image.shape
    image = torch.from_numpy(image).unsqueeze(dim=0)  # 1, c, h, w

    if h > w:
        half = (h - w) // 2
        cropsize = (0, half, w, half + w)  # left, upper, right, lower
    elif w >= h:
        half = (w - h) // 2
        cropsize = (half, 0, half + h, h)

    image = image[:, :, cropsize[1]:cropsize[3], cropsize[0]:cropsize[2]]
    image = F.interpolate(image, size=resolution, mode='bilinear', align_corners=False)

    return image.squeeze(dim=0).numpy()  # c, h, w

#----------------------------------------------------------------------------

class UCF101Wrapper(UCF101):
    def __init__(self,
                 root,
                 train,
                 resolution,
                 path,
                 n_frames=16,
                 fold=1,
                 max_size=None,     # Artificially limit the size of the dataset. None = no limit. Applied before xflip.
                 use_labels=False,    # Enable conditioning labels? False = label dimension is zero.
                 return_vid=False,    # True for evaluating FVD
                 time_saliency=False,
                 **super_kwargs,         # Additional arguments for the Dataset base class.
                 ):

        video_root = osp.join(os.path.join(root, 'train'))
        super(UCF101, self).__init__(video_root)
        if not 1 <= fold <= 3:
            raise ValueError("fold should be between 1 and 3, got {}".format(fold))

        root = root + '/train/'
        self.path = root
        name = video_root.split('/')[-1]
        self.name = name
        self.train = train
        self.fold = fold
        self.time_saliency = time_saliency
        self.resolution = resolution
        self.nframes = n_frames
        self.annotation_path = os.path.join(root, 'ucfTrainTestlist')
        self.classes = list(sorted(p for p in os.listdir(video_root) if osp.isdir(osp.join(video_root, p))))
        self.classes.remove('ucfTrainTestlist')
        class_to_idx = {self.classes[i]: i for i in range(len(self.classes))}
        self.samples = make_dataset(video_root, class_to_idx, ('avi',), is_valid_file=None)
        video_list = [x[0] for x in self.samples]
        self._use_labels = use_labels
        self._label_shape = None
        self._raw_labels = None
        self._raw_shape = [len(video_list)] + [3, resolution, resolution]
        self.num_channels = 3
        self.return_vid = return_vid

        frames_between_clips = 1 # if train else 16
        self.video_clips_fname = os.path.join(root, f'ucf_video_clips_{frames_between_clips}_{n_frames}_all.pkl')
        self.xflip = super_kwargs["xflip"]

        self._raw_idx = np.arange(self._raw_shape[0], dtype=np.int64)

        if not osp.exists(self.video_clips_fname):
            video_clips = VideoClips(
                video_paths=video_list,
                clip_length_in_frames=n_frames,
                frames_between_clips=frames_between_clips,
                num_workers=1
            )
            with open(self.video_clips_fname, 'wb') as f:
                pickle.dump(video_clips, f)
        else:
            with open(self.video_clips_fname, 'rb') as f:
                video_clips = pickle.load(f)

        indices = self._select_fold(video_list, self.annotation_path,
                                    fold, train)

        self.size = video_clips.subset(indices).num_clips()
        self.shuffle_indices = [i for i in range(self.size)]
        random.shuffle(self.shuffle_indices)
        self._need_init = True

    @property
    def has_labels(self):
        return self._use_labels

    @property
    def label_dim(self):
        if self._use_labels:
            return self.n_classes
        else:
            return 0

    @property
    def image_shape(self):
        return list(self._raw_shape[1:])

    @property
    def label_shape(self):
        if self._use_labels:
            return [self.n_classes]
        else:
            return [0]

    def get_label(self, idx):
        if self._need_init:
            self._init_dset()

        video_idx, clip_idx = self.video_clips.get_clip_location(idx)
        label = self.samples[self.indices[video_idx]][1]

        onehot = np.zeros(self.label_shape, dtype=np.float32)
        onehot[label] = 1
        return onehot

    def get_details(self, idx):
        d = dnnlib.EasyDict()
        d.raw_label = self.get_label(idx)
        return d

    def _select_fold(self, video_list, annotation_path, fold, train):
        name = "train" if train else "test"
        name = "{}list{:02d}.txt".format(name, fold)
        f = os.path.join(annotation_path, name)
        selected_files = []
        with open(f, "r") as fid:
            data = fid.readlines()
            data = [x.strip().split(" ") for x in data]
            data = [os.path.join(self.root, x[0]) for x in data]
            selected_files.extend(data)

        name = "train" if not train else "test"
        name = "{}list{:02d}.txt".format(name, fold)
        f = os.path.join(annotation_path, name)

        with open(f, "r") as fid:
            data = fid.readlines()
            data = [x.strip().split(" ") for x in data]
            data = [os.path.join(self.root, x[0]) for x in data]
            selected_files.extend(data)


        selected_files = set(selected_files)
        indices = [i for i in range(len(video_list)) if video_list[i] in selected_files]
        return indices

    @property
    def n_classes(self):
        return len(self.classes)

    def __len__(self):
        return self.size

    def _init_dset(self):
        with open(self.video_clips_fname, 'rb') as f:
            video_clips = pickle.load(f)
        video_list = [x[0] for x in self.samples]
        self.video_clips_metadata = video_clips.metadata
        self.indices = self._select_fold(video_list, self.annotation_path,
                                         self.fold, self.train)
        self.video_clips = video_clips.subset(self.indices)

        self._need_init = False
        # filter out the pts warnings
        warnings.filterwarnings('ignore')

    def _preprocess(self, video):
        video = resize_crop(video, self.resolution)

        if self.train and random.random() < 0.5 and self.xflip:
            video = torch.flip(video, [3])

        return video

    def __getitem__(self, idx):
        idx = self.shuffle_indices[idx]
        if self._need_init:
            self._init_dset()

        video, audio, info, video_idx = self.video_clips.get_clip(idx)
        video = video.permute(0, 3, 1, 2).float()  # [t, h, w, c] -> [t, c, h, w]
        video = self._preprocess(video)
        label = self.get_label(idx)

        if self.return_vid:
            return video[:, :16]

        if self.time_saliency:
            frames = [0, self.nframes - 1]
        else:
            frames = [np.random.beta(2, 1, size=1), np.random.beta(1, 2, size=1)]
            frames = [int(frames[0] * self.nframes), int(frames[1] * self.nframes)]
            frames.sort()

        img0, img1 = video[:, frames[0]], video[:, frames[1]]
        img0 = img0 / 255. * 2. - 1
        img0 = img0 / 255. * 2. - 1

        return img0, img1, label, label, max(frames) - min(frames)


class ImageFolderDataset(Dataset):
    def __init__(self,
                 path,  # Path to directory or zip.
                 resolution=None,
                 nframes=16,  # number of frames for each video.
                 loader=default_loader,  # loader for "sequence" of images
                 return_vid=False,  # True for evaluating FVD
                 **super_kwargs,  # Additional arguments for the Dataset base class.
                 ):

        self._path = path
        self._zipfile = None
        self.apply_resize = True

        classes, class_to_idx = find_classes(path)
        imgs = make_imageclip_dataset(path, nframes, class_to_idx, False)

        if len(imgs) == 0:
            raise(RuntimeError("Found 0 images in subfolders of: " + path + "\n"
                               "Supported image extensions are: " +
                               ",".join(IMG_EXTENSIONS)))

        self.imgs = imgs
        self.classes = classes
        self.class_to_idx = class_to_idx
        self.nframes = nframes
        self.loader = loader
        self.img_resolution = resolution
        self._path = path
        self._total_size = len(self.imgs) * 2 if super_kwargs["xflip"] else len(self.imgs)
        self._raw_shape = [self._total_size] + [3, resolution, resolution]
        self.xflip = super_kwargs["xflip"]
        self.return_vid = return_vid
        self.shuffle_indices = [i for i in range(self._total_size)]
        self.to_tensor = transforms.ToTensor()
        random.shuffle(self.shuffle_indices)

        if os.path.isdir(self._path):
            self._type = 'dir'
            self._all_fnames = {os.path.relpath(os.path.join(root, fname), start=self._path) for root, _dirs, files in os.walk(self._path) for fname in files}
        elif self._file_ext(self._path) == '.zip':
            self._type = 'zip'
            self._all_fnames = set(self._get_zipfile().namelist())
        else:
            raise IOError('Path must point to a directory or zip')

        PIL.Image.init()
        self._image_fnames = sorted(fname for fname in self._all_fnames if self._file_ext(fname) in PIL.Image.EXTENSION)
        if len(self._image_fnames) == 0:
            raise IOError('No image files found in the specified path')


        name = os.path.splitext(os.path.basename(self._path))[0]
        # raw_shape = [len(self._image_fnames)] + list(self._load_raw_image(0).shape)
        # if resolution is not None and (raw_shape[2] != resolution or raw_shape[3] != resolution):
        #     raise IOError('Image files do not match the specified resolution')
        print(name,self._raw_shape,super_kwargs)
        print(super)
        super().__init__()
        # super().__init__(name=name, raw_shape=self._raw_shape, **super_kwargs)

    @staticmethod
    def _file_ext(fname):
        return os.path.splitext(fname)[1].lower()


    def _open_file(self, fname):
        if self._type == 'dir':
            try:
                return open(fname, 'rb')
            except:
                return open(os.path.join(self._path, fname), 'rb')
        if self._type == 'zip':
            return self._get_zipfile().open(fname, 'r')
        return None

    def close(self):
        try:
            if self._zipfile is not None:
                self._zipfile.close()
        finally:
            self._zipfile = None

    def __getstate__(self):
        return dict(super().__getstate__(), _zipfile=None)

    def _load_raw_image(self, raw_idx):
        fname = self._image_fnames[raw_idx]
        with self._open_file(fname) as f:
            if pyspng is not None and self._file_ext(fname) == '.png':
                image = pyspng.load(f.read())
            else:
                image = np.array(PIL.Image.open(f))
        if image.ndim == 2:
            image = image[:, :, np.newaxis] # HW => HWC
        image = image.transpose(2, 0, 1) # HWC => CHW
        return image

    def _load_raw_labels(self):
        fname = 'dataset.json'
        if fname not in self._all_fnames:
            return None
        with self._open_file(fname) as f:
            labels = json.load(f)['labels']
        if labels is None:
            return None
        labels = dict(labels)
        labels = [labels[fname.replace('\\', '/')] for fname in self._image_fnames]
        labels = np.array(labels)
        labels = labels.astype({1: np.int64, 2: np.float32}[labels.ndim])
        return labels

    def _load_img_from_path(self, path):
        with self._open_file(path) as f:
            if pyspng is not None and self._file_ext(path) == '.png':
                img = pyspng.load(f.read())
                img = rearrange(img, 'h w c -> c h w')
            else:
                img = self.to_tensor(PIL.Image.open(f)).numpy() * 255 # c h w
        return img

    def __getitem__(self, index):
        """
        Args:
            index (int): Index
        Returns:
            tuple: (image, target) where target is class_index of the target class.
        """
        index = self.shuffle_indices[index]

        # clip is a list of 16 frames
        if self.xflip and index >= self._total_size // 2:
            clip = self.imgs[index - self._total_size // 2]
        else:
            clip = self.imgs[index]

        if self.return_vid:
            vid = np.stack([self._load_img_from_path(clip[i][0]) for i in range(self.nframes)], axis=0)
            if self.apply_resize:
                return resize_crop(torch.from_numpy(vid), resolution=self.img_resolution).numpy()
            return rearrange(vid, 't c h w -> c t h w')

        # frames = [np.random.beta(2, 1, size=1), np.random.beta(1, 2, size=1)]
        # frames = [int(frames[0] * self.nframes), int(frames[1] * self.nframes)]
        frames= [0, np.random.randint(low=1, high=self.nframes-1)]
        frames.sort()

        path0, target0 = clip[min(frames)]
        img0 = self._load_img_from_path(path0)

        path1, target1 = clip[max(frames)]
        img1 = self._load_img_from_path(path1)

        path1, target1 = clip[max(frames) - 1]
        img1_minus_1 = self._load_img_from_path(path1)

        if self.apply_resize:
            img0 = resize_crop_img(img0, self.img_resolution)
            img1 = resize_crop_img(img1, self.img_resolution)
            img1_minus_1 = resize_crop_img(img1_minus_1, self.img_resolution)
        if self.xflip and index >= self._total_size // 2:
            img0, img1, img1_minus_1 = img0[:, :, ::-1], img1[:, :, ::-1], img1_minus_1[:, :, ::-1]

        img0 = img0 / 255. * 2. - 1.
        img1 = img1 / 255. * 2. - 1.
        img1_minus_1 = img1_minus_1 / 255. * 2. - 1.
        return img0, img1, img1_minus_1, target0, target1, max(frames) - min(frames)

    def __len__(self):
        return self._total_size


import os

from typing import Any, List, Optional, Tuple, Union

import av
import PIL
import numpy as np

import torch
from torch.utils.data import Dataset


class VideoFolder(Dataset):
    IMG_EXTENSIONS = [
        ".png",
        ".PNG",
    ]
    VIDEO_EXTENSIONS = [
        ".mp4",
        ".MP4",
    ]

    def __init__(
        self,
        path: str,
        size: List[int],
        nframes: int = 128,
    ):
        if isinstance(size, (list, tuple)):
            if len(size) not in [1, 2]:
                raise ValueError(
                    f"Size must be an int or a 1 or 2 element tuple/list, not a {len(size)} element tuple/list"
                )

        if isinstance(size, int):
            size = [size, size]

        _path = path

        _all_fnames = {
            os.path.relpath(os.path.join(root, fname), start=_path)
            for root, _dirs, files in os.walk(_path)
            for fname in files
        }
        _video_fnames = sorted(
            fname
            for fname in _all_fnames
            if self._file_ext(fname) in self.VIDEO_EXTENSIONS
        ) + sorted(
            list(
                set(
                    (
                        os.path.dirname(fname)
                        for fname in _all_fnames
                        if self._file_ext(fname) in self.IMG_EXTENSIONS
                    )
                )
            )
        )
        _video_fnames = sorted(_video_fnames)

        self.path = path
        self.size = size
        self.nframes = nframes

        self._video_fnames = _video_fnames
        self._total_size = len(self._video_fnames)

    @staticmethod
    def _file_ext(fname):
        return os.path.splitext(fname)[1].lower()

    def __getitem__(self, index):
        video_path = os.path.join(self.path, self._video_fnames[index])
        video = []

        if os.path.isdir(video_path):
            _all_fnames = {
                os.path.relpath(os.path.join(root, fname), start=video_path)
                for root, _dirs, files in os.walk(video_path)
                for fname in files
            }
            _video_fnames = sorted(
                fname
                for fname in _all_fnames
                if self._file_ext(fname) in self.IMG_EXTENSIONS
            )

            for fname in _video_fnames:
                with open(os.path.join(video_path, fname), "rb") as f:
                    video.append(
                        np.array(
                            PIL.Image.open(f)
                            .convert("RGB")
                            .resize(
                                self.size, resample=3
                            )  # PIL.Image.Resampling.LANCZOS = 1 PIL.Image.Resampling.BICUBIC = 3
                        )
                    )
        else:
            container = av.open(video_path)
            container.streams.video[0].thread_type = "AUTO"

            total_frames = container.streams.video[0].frames
            frame_scale = total_frames / self.nframes
            frame_scaled_idxs = [int(i * frame_scale) for i in range(total_frames)]

            for idx, frame in enumerate(container.decode(video=0)):
                if idx in frame_scaled_idxs:
                    video.append(
                        np.asarray(
                            frame.to_image()
                            .convert("RGB")
                            .resize(
                                self.size, resample=3
                            )  # PIL.Image.Resampling.LANCZOS = 1 PIL.Image.Resampling.BICUBIC = 3
                        )
                    )

        video = np.stack(video).astype(np.float32) / 255.0
        return video

    def __len__(self):
        return self._total_size

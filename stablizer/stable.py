import numpy as np
import matplotlib.pyplot as plt
import cv2
import sys, time
import shapely.geometry as geometry
import stablizer.util as util
import stablizer.identify as identify
import stablizer.match as match
import stablizer.transform as transform
import stablizer.geometry as geometry

# Calculate the required shape of the image given a set of global
# affine transformation matrices and the original shape.
# Also returns the global matrices.
def image_dimensions(shape,gmatrix):
    assert(len(shape) == 2)
    s = shape[1::-1] # Save typing
    rectangle = np.array(( (0,0,1) , (0,s[1],1) , (*s,1) , (s[0],0,1) ))
    trect = np.einsum('ijk...,lk...->ilj...',gmatrix,rectangle)[:,:,:2]
    x = trect[:,:,0]
    y = trect[:,:,1]
    w = np.max(x)-np.min(x)
    h = np.max(y)-np.min(y)

    gm = np.array(gmatrix)
    gm[:,1,2] = gm[:,1,2] - np.min(y)
    gm[:,0,2] = gm[:,0,2] - np.min(x)
    return gm,int(w),int(h)


def frame_affine(keypoints_list,matches_list):
    kp    = keypoints_list
    matches = matches_list
    localmt = np.zeros((len(kp),3,3))
    invmatr = np.zeros((len(kp),3,3))
    gmatrix = np.zeros((len(kp),3,3))

    # Calculate local transformation matrices
    invmatr[0] = np.diag(np.ones(3))
    for i in range(1,len(kp)):
        try:
            localmt[i] = transform.affine_transform(kp[i-1],kp[i],matches[i-1])
        except ValueError as e:
            raise ValueError('Occured in frame {}'.format(i)) from e
        invmatr[i] = np.linalg.inv(localmt[i])
    
    # Calculate global transformation matrices
    gmatrix[0] = invmatr[0]
    for i in range(1,len(kp)):
        gmatrix[i] = gmatrix[i-1] @ invmatr[i] 

    return gmatrix

# This algorithm compares each frame against a previous 'fixed' frame.
# As the 'fixed' frame goes out of view, a new fixed frame is generated by
# averaging the last few frames.
def leapfrog_affine(video):
    kp      = [0]*video.shape[0]
    des     = [0]*(len(kp))
    gmatrix = np.zeros((len(kp),3,3))
    gmatrix[0,:,:] = np.diag(np.ones(3))
    
    # Identify keypoints and descriptors
    for k,frame in enumerate(video.read()):
        kp[k],des[k] = identify.detect_features(frame)

    f_frame = [0]     # Index of fixed frame, set first frame as initial
    fkp     = [kp[0]] # Key points in fixed frame
    fdes    = [des[0]] # Key points in fixed frame

    for i in range(1,len(kp)):
        try:
            matches = match.match(fkp[-1],fdes[-1],kp[i],des[i],
                    maxdist=video.shape[1]/3)
        except cv2.error as e:
            raise ValueError('Could not find enough feature points on frame {}'.format(i)) from e

        try:
            localmt = transform.affine_transform(fkp[-1],kp[i],matches)
        except Exception as e:
            raise Exception('The affine estimate is getting confused on frame {}'.format(i)) from e

        gmatrix[i] = gmatrix[f_frame[-1]] @ np.linalg.inv(localmt)
        


        
        # Calculate frame overlap with fixed frame 
        intersect_area = geometry.intersect(
                geometry.transformed_rect(video.shape[1:],gmatrix[f_frame[-1]]),
                geometry.transformed_rect(video.shape[1:],gmatrix[i])).area
        area = geometry.transformed_rect(video.shape[1:],gmatrix[f_frame[-1]]).area

        if intersect_area/area < 0.8:
            f_frame.append(i)
            fkp.append(kp[i])
            fdes.append(des[i])

    return gmatrix


  

def stablize_video(video,extra=False):
    kp    = [0]*video.shape[0]
    des   = [0]*video.shape[0]
    matches = [0]*(video.shape[0]-1)
    gmatrix = np.zeros((video.shape[0],3,3))

    # Identify keypoints and descriptors
    for k,frame in enumerate(video.read()):
        kp[k],des[k] = identify.detect_features(frame)
    print('Identified keypoints') 

    # Perform matching
    for i in range(1,video.shape[0]):
        try:
            matches[i-1] = match.match(kp[i-1],des[i-1],kp[i],des[i])
        except cv2.error as e:
            raise ValueError('Could not find enough feature points on frame {}'.format(i)) from e
    print('All matches completed')
 

    ###############
    # Uncomment to change transformation estimator
    ###############

    #gmatrix = leapfrog_affine(video)
    gmatrix = frame_affine(kp,matches)

    gmatrix,fx,fy = image_dimensions(video.shape[1:],gmatrix)
    print(fx,fy)

    # Transformation generators
    def stable_vid():
        for i,frame in enumerate(video.read()):
            yield cv2.warpPerspective(frame, gmatrix[i], (fx,fy))
    stable_vid = util.Video(stable_vid,(video.shape[0],fy,fx))
    
    # Mask generator
    def mask():
        base_mask  = np.ones(video.shape[1:],np.uint8)
        for i in range(video.shape[0]):
            yield cv2.warpPerspective(base_mask, gmatrix[i], (fx,fy))
    mask = util.Video(mask,(video.shape[0],fy,fx))

    if extra:
        extrainfo = {
                'mask':mask,
                'gmatrix':gmatrix
                }
        return stable_vid,extrainfo

    return stable_vid

if __name__=='__main__':
    video = util.VideoReader('resources/simnoise.mp4')
    print(video.shape)
    stablized_video,info = stablize_video(video,extra=True)
    print('video stablized')

    # Prepare video writer
    if '-fs' in sys.argv:
        filename = sys.argv[sys.argv.index('-fs')+1]
        stable_writer = util.VideoWriter(filename,stablized_video.shape[1:]) 
    else:
        stable_writer = util.VideoShower('stable')
    # Save gmatrix if requested
    if '-fm' in sys.argv:
        np.savetxt(sys.argv[sys.argv.index('-fm')+1],info['gmatrix'].reshape(-1,3))
   
    for frame in stablized_video.read():
        stable_writer.write(frame)

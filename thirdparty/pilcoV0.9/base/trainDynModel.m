%% trainDynModel.m
% *Summary:* Script to learn the dynamics model
%
% Copyright (C) 2008-2013 by 
% Marc Deisenroth, Andrew McHutchon, Joe Hall, and Carl Edward Rasmussen.
%
% Last modification: 2013-05-20
%
%% High-Level Steps
% # Extract states and controls from x-matrix
% # Define the training inputs and targets of the GP
% # Train the GP 

%% Code


% 1. Train GP dynamics model
Du = length(policy.maxU); Da = length(plant.angi); % no. of ctrl and angles
xaug = [x(:,dyno) x(:,end-Du-2*Da+1:end-Du)];     % x augmented with angles
dynmodel.inputs = [xaug(:,dyni) x(:,end-Du+1:end)];     % use dyni and ctrl
dynmodel.targets = y(:,dyno);
dynmodel.targets(:,difi) = dynmodel.targets(:,difi) - x(:,dyno(difi));

%dynmodel = dynmodel.train(dynmodel, plant, trainOpt);  %  train dynamics GP

dynmodel.hyp = [ 5.509997626011774,   5.695797298947197,   5.692015197025450,   5.638201671096178;
                 2.172712533907143,   4.666315918776204,   4.612552250383755,   4.942292180454226;
                 5.146257432313829,   2.296298649465123,   2.456701520947445,   2.614396832176411;
                 2.890900478963427,   0.214158569334003,   0.224852498280747,   0.493471679361221;
                 1.797786347384082,   1.586977710450885,  -0.058171149174515,  -0.345601213254841;
                 4.069820471633279,   3.013197670171895,   2.934665512611971,   3.616775778040698;
                -0.984097642284717,   0.137890342258600,   1.299603816102084,  -0.611523812016797;
                -4.115382429098694,  -4.115897287345494,  -3.168863634366222,  -4.247869488525884];
% display some hyperparameters
Xh = dynmodel.hyp;
% noise standard deviations
disp(['Learned noise std: ' num2str(exp(Xh(end,:)))]);
% signal-to-noise ratios (values > 500 can cause numerical problems)
disp(['SNRs             : ' num2str(exp(Xh(end-1,:)-Xh(end,:)))]);

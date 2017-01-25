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


dynmodel.hyp = [ 3.9334647319001053,  4.5962208952767361,  4.5757167559443719,  4.522758095118883 ;
     2.1930205630295228,  4.4807511470653818,  4.4351067151870973,  4.4540868687600463;
     4.3608500872310714,  2.2913983610348976,  2.2121449195734262,  2.8774064220875402;
     1.7948699393302578,  0.1481708848589371, -0.1783972811856207,  0.4476707345109228;
     0.708264761311401 ,  0.5984618033038871,  0.2448532478410302,  0.6136837551595212;
     4.352372300760937 ,  3.7101295723612826,  3.1019605818763454,  3.8688729263847725;
    -1.0625703272361062,  0.5743589103063288,  1.299018064190927 , -0.2962819487355654;
    -4.0952999668577625, -3.9585193039557112, -3.1587713592239135, -4.2794694830096214];

%dynmodel = dynmodel.train(dynmodel, plant, trainOpt);  %  train dynamics GP
% display some hyperparameters
Xh = dynmodel.hyp;
% noise standard deviations
disp(['Learned noise std: ' num2str(exp(Xh(end,:)))]);
% signal-to-noise ratios (values > 500 can cause numerical problems)
disp(['SNRs             : ' num2str(exp(Xh(end-1,:)-Xh(end,:)))]);

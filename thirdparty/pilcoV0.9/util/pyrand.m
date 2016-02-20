function R = pyrand(varargin)
    if nargin > 1
        dims = cellfun(@int32,varargin);
    else
        dims = varargin{1};
    end
    R = cellfun(@double,cell( py.numpy.random.rand(prod(dims)).tolist()));
    R = reshape(R,dims);

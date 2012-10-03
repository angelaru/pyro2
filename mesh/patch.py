"""
The patch module allows for a grid to be created and for data to be
defined on that grid.

Typical usage:

create the grid

   grid = grid2d(nx, ny)


create the data that lives on that grid

   data = ccData2d(grid)

   bcObj = bcObject(xlb="reflect", xrb="reflect", 
                    ylb="outflow", yrb="outflow")
   data.registerVar("density", bcObj)
   ...

   data.create()


initialize some data

   dens = data.getVarPtr("density")
   dens[:,:] = ...


fill the ghost cells

   data.fillBC("density")

"""

import numpy
import pickle
from util import msg


class bcObject:
    """
    boundary condition container -- hold the BCs on each boundary
    for a single variable
    """

    def __init__ (self, xlb="outflow", xrb="outflow", 
                  ylb="outflow", yrb="outflow"):

        valid = ["outflow", "periodic", "reflect-even", "reflect-odd"]

        # -x boundary
        if (xlb in valid):
            self.xlb = xlb
        else:
            msg.fail("ERROR: xlb = %s invalid BC" % (xlb))

        # +x boundary
        if (xrb in valid):
            self.xrb = xrb
        else:
            msg.fail("ERROR: xrb = %s invalid BC" % (xrb))

        # -y boundary
        if (ylb in valid):
            self.ylb = ylb
        else:
            msg.fail("ERROR: ylb = %s invalid BC" % (ylb))

        # +y boundary
        if (yrb in valid):
            self.yrb = yrb
        else:
            msg.fail("ERROR: yrb = %s invalid BC" % (yrb))


        # periodic checks
        if ((xlb == "periodic" and not xrb == "periodic") or
            (xrb == "periodic" and not xlb == "periodic")):
            msg.fail("ERROR: both xlb and xrb must be periodic")

        if ((ylb == "periodic" and not yrb == "periodic") or
            (yrb == "periodic" and not ylb == "periodic")):
            msg.fail("ERROR: both ylb and yrb must be periodic")

    def __str__(self):
        """ print out some basic information about the BC object """

        string = "BCs: -x: %s  +x: %s  -y: %s  +y: %s" % \
            (self.xlb, self.xrb, self.ylb, self.yrb)

        return string

    
class grid2d:
    """
    the 2-d grid class.  The grid object will contain the coordinate
    information (at various centerings).

    A basic (1-d) representation of the layout is:

    |     |      |     X     |     |      |     |     X     |      |     |
    +--*--+- // -+--*--X--*--+--*--+- // -+--*--+--*--X--*--+- // -+--*--+
       0          ng-1    ng   ng+1         ... ng+nx-1 ng+nx      2ng+nx-1

                         ilo                      ihi
    
    |<- ng guardcells->|<---- nx interior zones ----->|<- ng guardcells->|

    The '*' marks the data locations.
    """

    def __init__ (self, nx, ny, ng=1, \
                  xmin=0.0, xmax=1.0, ymin=0.0, ymax=1.0):

        """
        The class constructor function.

        The only data that we require is the number of points that
        make up the mesh.

        We optionally take the extrema of the domain (assume it is
        [0,1]x[0,1]), number of ghost cells (assume 1), and the
        type of boundary conditions (assume reflecting).
        """

        # time 
        self.t = -1.0

        # size of grid
        self.nx = nx
        self.ny = ny
        self.ng = ng

        self.qx = 2*ng+nx
        self.qy = 2*ng+ny

        
        # domain extrema
        self.xmin = xmin
        self.xmax = xmax

        self.ymin = ymin
        self.ymax = ymax

        # compute the indices of the block interior (excluding guardcells)
        self.ilo = ng
        self.ihi = ng+nx-1

        self.jlo = ng
        self.jhi = ng+ny-1

        # define the coordinate information at the left, center, and right
        # zone coordinates
        self.dx = (xmax - xmin)/nx

        self.xl = (numpy.arange(nx+2*ng) - ng)*self.dx + xmin
        self.xr = (numpy.arange(nx+2*ng) + 1.0 - ng)*self.dx + xmin
        self.x = 0.5*(self.xl + self.xr)

        self.dy = (ymax - ymin)/ny

        self.yl = (numpy.arange(ny+2*ng) - ng)*self.dy + ymin
        self.yr = (numpy.arange(ny+2*ng) + 1.0 - ng)*self.dy + ymin
        self.y = 0.5*(self.yl + self.yr)

        # 2-d versions of the zone coordinates
        x2d = numpy.repeat(self.x, 2*self.ng+self.ny)
        x2d.shape = (2*self.ng+self.nx, 2*self.ng+self.ny)
        self.x2d = x2d

        y2d = numpy.repeat(self.y, 2*self.ng+self.nx)
        y2d.shape = (2*self.ng+self.ny, 2*self.ng+self.nx)
        y2d = numpy.transpose(y2d)
        self.y2d = y2d
                                                                        

    def __str__(self):
        """ print out some basic information about the grid object """

        string = "2-d grid: nx = " + `self.nx` + \
                         ", ny = " + `self.ny` + \
                         ", ng = " + `self.ng`

        return string
        

class ccData2d:
    """
    the cell-centered data that lives on a grid.

    a ccData2d object is built in a multi-step process before it can
    be used.

    create the object.  We pass in a grid object to describe where the 
    data lives:

        myData = patch.ccData2d(myGrid)

    register any variables that we expect to live on this patch.  Here
    bcObject describes the boundary conditions for that variable.

        myData.registerVar('density', bcObject)
        myData.registerVar('x-momentum', bcObject)
        ...


    Register any auxillary data -- these are any parameters that are
    needed to interpret the data outside of the simulation (for
    example, the gamma for the equation of state).

        mydata.setAux(keyword, value)

    finally, finish the initialization of the patch

        myPatch.create()

    This last step actually allocates the storage for the state
    variables.  Once this is done, the patch is considered to be
    locked.  New variables cannot be added.
    
    """

    def __init__ (self, grid, dtype=numpy.float64):

        """
        The class constructor function.

        The only data that we require is grid object that describes
        the geometry of the domain.
        """
        
        self.grid = grid

        self.dtype = dtype
        self.data = None

        self.vars = []
        self.nvar = 0

        self.aux = {}

        self.BCs = {}

        self.initialized = 0


    def registerVar(self, name, bcObject):
        """ 
        register a variable with ccData2d object.  Here we pass in a
        bcObject that describes the boundary conditions for that
        variable.
        """

        if (self.initialized == 1):
            msg.fail("ERROR: grid already initialized")

        self.vars.append(name)
        self.nvar += 1

        self.BCs[name] = bcObject


    def setAux(self, keyword, value):
        """ 
        set any auxillary (scalar) data
        """
        self.aux[keyword] = value


    def create(self):
        """
        called after all the variables are registered and allocates
        the storage for the state data
        """

        if (self.initialized) == 1:
            msg.fail("ERROR: grid already initialized")

        self.data = numpy.zeros((self.nvar,
                                 2*self.grid.ng+self.grid.nx, 
                                 2*self.grid.ng+self.grid.ny),
                                dtype=self.dtype)
        self.initialized = 1

        
    def __str__(self):
        """ print out some basic information about the ccData2d object """

        if (self.initialized == 0):
            myStr = "ccData2d object not yet initialized"
            return myStr

        myStr = "cc data: nx = " + `self.grid.nx` + \
                       ", ny = " + `self.grid.ny` + \
                       ", ng = " + `self.grid.ng` + "\n" + \
                 "         nvars = " + `self.nvar` + "\n" + \
                 "         variables: \n" 
                 
        ilo = self.grid.ilo
        ihi = self.grid.ihi
        jlo = self.grid.jlo
        jhi = self.grid.jhi

        n = 0
        while (n < self.nvar):
            myStr += "      %16s: min: %15.10f    max: %15.10f\n" % \
                (self.vars[n],
                 numpy.min(self.data[n,ilo:ihi,jlo:jhi]), 
                 numpy.max(self.data[n,ilo:ihi,jlo:jhi]) )
            myStr += "      %16s  BCs: -x: %-12s +x: %-12s -y: %-12s +y: %-12s\n" %\
                (" " , self.BCs[self.vars[n]].xlb, 
                       self.BCs[self.vars[n]].xrb, 
                       self.BCs[self.vars[n]].ylb, 
                       self.BCs[self.vars[n]].yrb)
            n += 1
 
        return myStr
    

    def getVarPtr(self, name):
        """
        return a pointer to the data array for the variable described
        by name.  Any changes made to this are automatically reflected
        in the ccData2d object
        """
        n = self.vars.index(name)
        return self.data[n,:,:]

    
    def getAux(self, keyword):
        if keyword in self.aux.keys():
            return self.aux[keyword]
        else:
            return None
        

    def getVarPtrByIndex(self, index):
        return self.data[index,:,:]


    def zero(self, name):
        n = self.vars.index(name)
        self.data[n,:,:] = 0.0
        

    def fillBCAll(self):
        """
        fill boundary conditions on all variables
        """
        for name in self.vars:
            self.fillBC(name)

                
    def fillBC(self, name):
        """ 
        fill the boundary conditions.  This operates on a single state
        variable at a time, to allow for maximum flexibility

        we do periodic, reflect-even, reflect-odd, and outflow

        each variable name has a corresponding bcObject stored in the
        ccData2d object -- we refer to this to figure out the action
        to take at each boundary.
        """
    
        # there is only a single grid, so every boundary is on
        # a physical boundary (except if we are periodic)
    
        n = self.vars.index(name)

        # -x boundary
        if (self.BCs[name].xlb == "outflow"):

            i = 0
            while i < self.grid.ilo:
                self.data[n,i,:] = self.data[n,self.grid.ilo,:]
                i += 1                

        elif (self.BCs[name].xlb == "reflect-even"):
        
            i = 0
            while i < self.grid.ilo:
                self.data[n,i,:] = self.data[n,2*self.grid.ng-i-1,:]
                i += 1

        elif (self.BCs[name].xlb == "reflect-odd"):
        
            i = 0
            while i < self.grid.ilo:
                self.data[n,i,:] = -self.data[n,2*self.grid.ng-i-1,:]
                i += 1

        elif (self.BCs[name].xlb == "periodic"):

            i = 0
            while i < self.grid.ilo:
                self.data[n,i,:] = self.data[n,self.grid.ihi-self.grid.ng+i+1,:]
                i += 1
            

        # +x boundary
        if (self.BCs[name].xrb == "outflow"):

            i = self.grid.ihi+1
            while i < self.grid.nx+2*self.grid.ng:
                self.data[n,i,:] = self.data[n,self.grid.ihi,:]
                i += 1
                
        elif (self.BCs[name].xrb == "reflect-even"):

            i = 0
            while i < self.grid.ng:
                i_bnd = self.grid.ihi+1+i
                i_src = self.grid.ihi-i

                self.data[n,i_bnd,:] = self.data[n,i_src,:]
                i += 1

        elif (self.BCs[name].xrb == "reflect-odd"):

            i = 0
            while i < self.grid.ng:
                i_bnd = self.grid.ihi+1+i
                i_src = self.grid.ihi-i
                
                self.data[n,i_bnd,:] = -self.data[n,i_src,:]
                i += 1

        elif (self.BCs[name].xrb == "periodic"):

            i = self.grid.ihi+1
            while i < 2*self.grid.ng + self.grid.nx:
                self.data[n,i,:] = self.data[n,i-self.grid.ihi-1+self.grid.ng,:]
                i += 1


        # -y boundary
        if (self.BCs[name].ylb == "outflow"):

            j = 0
            while j < self.grid.jlo:
                self.data[n,:,j] = self.data[n,:,self.grid.jlo]
                j += 1
                
        elif (self.BCs[name].ylb == "reflect-even"):

            j = 0
            while j < self.grid.jlo:
                self.data[n,:,j] = self.data[n,:,2*self.grid.ng-j-1]
                j += 1

        elif (self.BCs[name].ylb == "reflect-odd"):

            j = 0
            while j < self.grid.jlo:
                self.data[n,:,j] = -self.data[n,:,2*self.grid.ng-j-1]
                j += 1

        elif (self.BCs[name].ylb == "periodic"):

            j = 0
            while j < self.grid.jlo:
                self.data[n,:,j] = self.data[n,:,self.grid.jhi-self.grid.ng+j+1]
                j += 1
                

        # +y boundary
        if (self.BCs[name].yrb == "outflow"):

            j = self.grid.jhi+1
            while j < self.grid.ny+2*self.grid.ng:
                self.data[n,:,j] = self.data[n,:,self.grid.jhi]
                j += 1

        elif (self.BCs[name].yrb == "reflect-even"):

            j = 0
            while j < self.grid.ng:
                j_bnd = self.grid.jhi+1+j
                j_src = self.grid.jhi-j

                self.data[n,:,j_bnd] = self.data[n,:,j_src]
                j += 1

        elif (self.BCs[name].yrb == "reflect-odd"):

            j = 0
            while j < self.grid.ng:
                j_bnd = self.grid.jhi+1+j
                j_src = self.grid.jhi-j

                self.data[n,:,j_bnd] = -self.data[n,:,j_src]
                j += 1
        
        elif (self.BCs[name].yrb == "periodic"):

            j = self.grid.jhi+1
            while j < 2*self.grid.ng + self.grid.ny:
                self.data[n,:,j] = self.data[n,:,j-self.grid.jhi-1+self.grid.ng]
                j += 1


    def write(self, filename):
        """
        write out the ccData2d object to disk, stored in the file
        filename.  We use a python binary format (via pickle).  This
        stores a representation of the entire object.
        """
        pF = open(filename + ".pyro", "wb")
        pickle.dump(self, pF, pickle.HIGHEST_PROTOCOL)
        pF.close()


    def prettyPrint(self, varname):
        """
        print out a small dataset to the screen with the ghost cells
        a different color, to make things stand out
        """

        a = self.getVarPtr(varname)

        if (self.dtype == numpy.int):
            fmt = "%4d"
        elif (self.dtype == numpy.float64):
            fmt = "%12.6g"
        else:
            msg.fail("ERROR: dtype not supported")
        
        j = 0
        while j < self.grid.qy:
            i = 0
            while i < self.grid.qx:

                if (j < self.grid.jlo or j > self.grid.jhi or
                    i < self.grid.ilo or i > self.grid.ihi):
                    gc = 1
                else:
                    gc = 0

                if (gc):
                    print "\033[31m" + fmt % (a[i,j]) + "\033[0m" ,
                else:
                    print fmt % (a[i,j]) ,

                i += 1

            print " "
            j += 1

def read(filename):
    """
    read a ccData object from a file and return it and the grid info
    """

    # if we come in with .pyro, we don't need to add it again
    if (filename.find(".pyro") < 0):
        filename += ".pyro"

    pF = open(filename, "rb")
    data = pickle.load(pF)
    pF.close()
    
    return data.grid, data


def ccDataClone(old):

    if (not isinstance(old, ccData2d)):
        msg.fail("Can't clone object")

    new = ccData2d(old.grid)

    n = 0
    while (n < old.nvar):
        new.registerVar(old.vars[n], old.BCs[old.vars[n]])
        n += 1

    new.create()

    return new
        

if __name__== "__main__":

    # illustrate basic mesh operations

    myg = grid2d(16,32, xmax=1.0, ymax=2.0)

    mydata = ccData2d(myg)

    bc = bcObject()

    mydata.registerVar("a", bc)
    mydata.create()


    a = mydata.getVarPtr("a")
    a[:,:] = numpy.exp(-(myg.x2d - 0.5)**2 - (myg.y2d - 1.0)**2)

    print mydata

    # output
    print "writing\n"
    mydata.write("mesh_test")

    print "reading\n"
    myg2, myd2 = read("mesh_test")
    print myd2
